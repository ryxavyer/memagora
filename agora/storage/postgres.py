"""Postgres implementation of :class:`AgoraStore` — the reference backend.

psycopg 3 with hand-written SQL and no ORM, matching the house style on the
palace side (``mempalace/knowledge_graph.py`` is raw sqlite3). The SQL an
operator reads in ``docs/deployment.md`` is the SQL that runs.

Concurrency model: one connection, guarded by a lock, opened on first use —
the same shape as the palace's knowledge graph. A single uvicorn worker
serializes its database work through it. Deployments that outgrow that should
run more workers (each gets its own connection) or add ``psycopg_pool``, which
is a drop-in change confined to ``_connection``.
"""

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from dataclasses import replace
from typing import Iterator, Optional

import psycopg

from .base import (
    FACT_COLUMNS,
    NULL_SORT_KEY,
    AgoraStore,
    ApiKeyRecord,
    ConfigurationError,
    FactPage,
    FactQuery,
    MigrationError,
    PutResult,
    StoreClosedError,
    StoredFact,
    StoreHealth,
    build_fact_filters,
    decode_cursor,
    encode_cursor,
    load_migrations,
    normalize_fact,
    split_statements,
    validate_fact,
)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "postgres"

# recorded_at is TIMESTAMPTZ in Postgres but ISO text everywhere else in the
# system (the wire format, the cursors, the SQLite store). Casting on the way
# out keeps one representation above the storage seam.
_RECORDED_AT_TEXT = (
    "to_char(recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') || '+00:00'"
)

_SELECT_COLUMNS = FACT_COLUMNS.replace("recorded_at", f"{_RECORDED_AT_TEXT} AS recorded_at")


class PostgresStore(AgoraStore):
    name = "postgres"
    spec_version = "1.0"
    capabilities = frozenset(
        {
            "supports_export",
            "supports_partial_unique",
            "server_mode",
            "requires_external_service",
        }
    )

    def __init__(self, *, dsn: str):
        # No I/O in __init__ (RFC 001 §2.6).
        self._dsn = dsn
        self._conn: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_config(cls, config) -> "PostgresStore":
        if not config.dsn:
            raise ConfigurationError(
                "AGORA_STORE=postgres requires AGORA_DSN "
                "(e.g. postgresql://agora:secret@db:5432/agora)"
            )
        return cls(dsn=config.dsn)

    # ── Connection ──────────────────────────────────────────────────────

    @contextmanager
    def _connection(self):
        """Yield the guarded connection, connecting on first use."""
        with self._lock:
            if self._closed:
                raise StoreClosedError("store is closed")
            if self._conn is None or self._conn.closed:
                self._conn = psycopg.connect(self._dsn, autocommit=False)
            yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
            self._conn = None
            self._closed = True

    # ── Schema ──────────────────────────────────────────────────────────

    def migrate(self) -> list[str]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
                conn.commit()
                cur.execute("SELECT version FROM schema_migrations")
                applied = {row[0] for row in cur.fetchall()}

            done = []
            for version, sql in load_migrations(_MIGRATIONS_DIR):
                if version in applied:
                    continue
                try:
                    with conn.cursor() as cur:
                        for statement in split_statements(sql):
                            cur.execute(statement)
                        cur.execute(
                            "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                        )
                    conn.commit()
                except psycopg.Error as exc:
                    conn.rollback()
                    raise MigrationError(f"migration {version} failed: {exc}") from exc
                done.append(version)
            return done

    def truncate_all(self) -> None:
        """Wipe every table. Test-only helper — the conformance suite needs a
        clean database between cases, and a shared Postgres has no equivalent
        of SQLite's throwaway file."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE facts, api_keys")
            conn.commit()

    # ── Facts ───────────────────────────────────────────────────────────

    def put_facts(
        self,
        *,
        deployment_id: str,
        engineer_id: str,
        facts: list[StoredFact],
    ) -> PutResult:
        accepted = 0
        reasons: dict[str, int] = {}

        with self._connection() as conn:
            for raw in facts:
                fact = normalize_fact(
                    replace(raw, deployment_id=deployment_id, engineer_id=engineer_id)
                )
                reason = validate_fact(fact)
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"INSERT INTO facts ({FACT_COLUMNS}) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                fact.fact_id,
                                fact.deployment_id,
                                fact.engineer_id,
                                fact.subject,
                                fact.predicate,
                                fact.object,
                                fact.schema_version,
                                fact.recorded_at,
                                fact.valid_from,
                                fact.valid_to,
                                float(fact.confidence),
                                fact.source_session_id,
                            ),
                        )
                    conn.commit()
                except psycopg.errors.UniqueViolation:
                    conn.rollback()
                    reasons["duplicate_open_triple"] = reasons.get("duplicate_open_triple", 0) + 1
                    continue
                except psycopg.Error:
                    conn.rollback()
                    raise
                accepted += 1

        return PutResult(accepted=accepted, rejected=sum(reasons.values()), reasons=reasons)

    def get_facts(self, *, deployment_id: str, query: FactQuery) -> FactPage:
        clauses, params = build_fact_filters(deployment_id=deployment_id, query=query, ph="%s")

        if query.cursor:
            recorded_at, fact_id = decode_cursor(query.cursor, expected=2)
            clauses.append(
                "(recorded_at < %s::timestamptz OR "
                "(recorded_at = %s::timestamptz AND fact_id < %s))"
            )
            params.extend([recorded_at, recorded_at, fact_id])

        limit = max(1, query.limit)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM facts WHERE {' AND '.join(clauses)} "
            "ORDER BY recorded_at DESC, fact_id DESC LIMIT %s"
        )
        rows = self._fetch(sql, (*params, limit + 1))
        return _page(rows, limit=limit, cursor_of=lambda f: (f.recorded_at, f.fact_id))

    def timeline(
        self,
        *,
        deployment_id: str,
        subject: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> FactPage:
        clauses = ["deployment_id = %s"]
        params: list = [deployment_id]
        if subject:
            clauses.append("(subject = %s OR object = %s)")
            params.extend([subject, subject])

        sort = f"COALESCE(valid_from, '{NULL_SORT_KEY}')"
        if cursor:
            sort_key, fact_id = decode_cursor(cursor, expected=2)
            clauses.append(f"({sort} > %s OR ({sort} = %s AND fact_id > %s))")
            params.extend([sort_key, sort_key, fact_id])

        limit = max(1, limit)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM facts WHERE {' AND '.join(clauses)} "
            f"ORDER BY {sort} ASC, fact_id ASC LIMIT %s"
        )
        rows = self._fetch(sql, (*params, limit + 1))
        return _page(
            rows,
            limit=limit,
            cursor_of=lambda f: (f.valid_from or NULL_SORT_KEY, f.fact_id),
        )

    def export_facts(self, *, deployment_id: str) -> Iterator[StoredFact]:
        rows = self._fetch(
            f"SELECT {_SELECT_COLUMNS} FROM facts WHERE deployment_id = %s "
            "ORDER BY recorded_at ASC, fact_id ASC",
            (deployment_id,),
        )
        for row in rows:
            yield _to_fact(row)

    def count_facts(self, *, deployment_id: str) -> int:
        rows = self._fetch("SELECT COUNT(*) FROM facts WHERE deployment_id = %s", (deployment_id,))
        return int(rows[0][0])

    # ── API keys ────────────────────────────────────────────────────────

    _KEY_COLUMNS = "key_id, key_hash, deployment_id, engineer_id, created_at, revoked_at"

    def get_api_key(self, *, key_id: str) -> Optional[ApiKeyRecord]:
        rows = self._fetch(f"SELECT {self._KEY_COLUMNS} FROM api_keys WHERE key_id = %s", (key_id,))
        return _to_key(rows[0]) if rows else None

    def put_api_key(self, *, record: ApiKeyRecord) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO api_keys ({self._KEY_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        record.key_id,
                        record.key_hash,
                        record.deployment_id,
                        record.engineer_id,
                        record.created_at,
                        record.revoked_at,
                    ),
                )
            conn.commit()

    def revoke_api_key(self, *, key_id: str) -> bool:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE api_keys SET revoked_at = now() "
                    "WHERE key_id = %s AND revoked_at IS NULL",
                    (key_id,),
                )
                changed = cur.rowcount > 0
            conn.commit()
        return changed

    def list_api_keys(self, *, deployment_id: str) -> list[ApiKeyRecord]:
        rows = self._fetch(
            f"SELECT {self._KEY_COLUMNS} FROM api_keys WHERE deployment_id = %s "
            "ORDER BY created_at ASC",
            (deployment_id,),
        )
        return [_to_key(row) for row in rows]

    # ── Health ──────────────────────────────────────────────────────────

    def health(self) -> StoreHealth:
        started = time.monotonic()
        try:
            self._fetch("SELECT 1", ())
        except StoreClosedError:
            return StoreHealth.unhealthy("store is closed", backend=self.name)
        except psycopg.Error as exc:
            return StoreHealth.unhealthy(str(exc), backend=self.name)
        elapsed = (time.monotonic() - started) * 1000
        # The DSN carries a password — report the database name only.
        return StoreHealth.healthy(
            backend=self.name,
            detail=_safe_dsn_summary(self._dsn),
            latency_ms=round(elapsed, 2),
        )

    # ── Internals ───────────────────────────────────────────────────────

    def _fetch(self, sql: str, params) -> list:
        with self._connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                conn.commit()
            except psycopg.Error:
                conn.rollback()
                raise
        return rows


# ── Row mapping ─────────────────────────────────────────────────────────
#
# Columns come back positionally in FACT_COLUMNS order.

_FACT_ORDER = [c.strip() for c in FACT_COLUMNS.split(",")]


def _to_fact(row) -> StoredFact:
    values = dict(zip(_FACT_ORDER, row))
    return StoredFact(
        fact_id=values["fact_id"],
        deployment_id=values["deployment_id"],
        engineer_id=values["engineer_id"],
        subject=values["subject"],
        predicate=values["predicate"],
        object=values["object"],
        schema_version=values["schema_version"],
        recorded_at=values["recorded_at"],
        valid_from=values["valid_from"],
        valid_to=values["valid_to"],
        confidence=float(values["confidence"]),
        source_session_id=values["source_session_id"],
    )


def _to_key(row) -> ApiKeyRecord:
    key_id, key_hash, deployment_id, engineer_id, created_at, revoked_at = row
    return ApiKeyRecord(
        key_id=key_id,
        key_hash=key_hash,
        deployment_id=deployment_id,
        engineer_id=engineer_id,
        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        revoked_at=revoked_at.isoformat() if hasattr(revoked_at, "isoformat") else revoked_at,
    )


def _page(rows, *, limit: int, cursor_of) -> FactPage:
    facts = [_to_fact(r) for r in rows[:limit]]
    next_cursor = encode_cursor(*cursor_of(facts[-1])) if len(rows) > limit and facts else None
    return FactPage(facts=facts, next_cursor=next_cursor)


def _safe_dsn_summary(dsn: str) -> str:
    """Database name and host, never credentials."""
    tail = dsn.rsplit("@", 1)[-1]
    return tail or "postgres"
