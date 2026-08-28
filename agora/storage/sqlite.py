"""SQLite implementation of :class:`AgoraStore`.

Stdlib only. This is the store for local development, the default test suite,
and small single-team deployments that do not want to run Postgres — not a
second-class citizen: it passes the same conformance suite as the reference
implementation, including the partial-unique-index dedup rule.

Concurrency mirrors ``mempalace/knowledge_graph.py``: one cached connection in
WAL mode with ``check_same_thread=False``, guarded by a lock around every
statement. That is adequate for a single-process uvicorn worker; teams
expecting concurrent writers should run Postgres.
"""

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional

from .base import (
    DECISION_COLUMNS,
    FACT_COLUMNS,
    AgoraStore,
    ApiKeyRecord,
    FactPage,
    FactQuery,
    MigrationError,
    NULL_SORT_KEY,
    DecisionPage,
    DecisionQuery,
    PutResult,
    StoreClosedError,
    StoredDecision,
    StoredFact,
    StoreHealth,
    build_fact_filters,
    decode_cursor,
    encode_cursor,
    load_migrations,
    _normalized_triple,
    normalize_fact,
    split_statements,
    today_iso,
    validate_decision,
    validate_fact,
)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "sqlite"


class SQLiteStore(AgoraStore):
    name = "sqlite"
    spec_version = "1.0"
    migrations_dir = _MIGRATIONS_DIR
    capabilities = frozenset(
        {
            "supports_export",
            "supports_partial_unique",
            "local_mode",
        }
    )

    def __init__(self, *, path: str):
        # No I/O in __init__ (RFC 001 §2.6) — the connection opens lazily.
        self._path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_config(cls, config) -> "SQLiteStore":
        return cls(path=config.sqlite_path)

    # ── Connection ──────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise StoreClosedError("store is closed")
        if self._conn is None:
            if self._path != ":memory:":
                Path(self._path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._closed = True

    # ── Schema ──────────────────────────────────────────────────────────

    def applied_migrations(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if row is None:
                return []
            return [
                r["version"]
                for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ]

    def migrate(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            done = []
            for version, sql in load_migrations(_MIGRATIONS_DIR):
                if version in applied:
                    continue
                try:
                    with conn:
                        for statement in split_statements(sql):
                            conn.execute(statement)
                        conn.execute(
                            "INSERT INTO schema_migrations (version, applied_at) "
                            "VALUES (?, datetime('now'))",
                            (version,),
                        )
                except sqlite3.Error as exc:
                    raise MigrationError(f"migration {version} failed: {exc}") from exc
                done.append(version)
            return done

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

        with self._lock:
            conn = self._connect()
            for raw in facts:
                # Provenance is server-derived: whatever the client sent for
                # these fields is overwritten, never trusted.
                fact = normalize_fact(
                    replace(raw, deployment_id=deployment_id, engineer_id=engineer_id)
                )
                reason = validate_fact(fact)
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
                try:
                    with conn:
                        conn.execute(
                            f"INSERT INTO facts ({FACT_COLUMNS}) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                                fact.decision_id,
                            ),
                        )
                except sqlite3.IntegrityError:
                    reasons["duplicate_open_triple"] = reasons.get("duplicate_open_triple", 0) + 1
                    continue
                accepted += 1

        return PutResult(
            accepted=accepted,
            rejected=sum(reasons.values()),
            reasons=reasons,
        )

    def get_facts(self, *, deployment_id: str, query: FactQuery) -> FactPage:
        clauses, params = build_fact_filters(deployment_id=deployment_id, query=query)

        if query.cursor:
            recorded_at, fact_id = decode_cursor(query.cursor, expected=2)
            clauses.append("(recorded_at < ? OR (recorded_at = ? AND fact_id < ?))")
            params.extend([recorded_at, recorded_at, fact_id])

        limit = max(1, query.limit)
        sql = (
            f"SELECT {FACT_COLUMNS} FROM facts WHERE {' AND '.join(clauses)} "
            "ORDER BY recorded_at DESC, fact_id DESC LIMIT ?"
        )
        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, (*params, limit + 1)).fetchall()

        return _page(rows, limit=limit, cursor_of=lambda f: (f.recorded_at, f.fact_id))

    def timeline(
        self,
        *,
        deployment_id: str,
        subject: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> FactPage:
        clauses = ["deployment_id = ?"]
        params: list = [deployment_id]
        if subject:
            clauses.append("(subject = ? OR object = ?)")
            params.extend([subject, subject])

        # COALESCE gives unbounded starts a sort key that lands last, which
        # makes the ordering keyset-paginable in one expression.
        sort = f"COALESCE(valid_from, '{NULL_SORT_KEY}')"
        if cursor:
            sort_key, fact_id = decode_cursor(cursor, expected=2)
            clauses.append(f"({sort} > ? OR ({sort} = ? AND fact_id > ?))")
            params.extend([sort_key, sort_key, fact_id])

        limit = max(1, limit)
        sql = (
            f"SELECT {FACT_COLUMNS} FROM facts WHERE {' AND '.join(clauses)} "
            f"ORDER BY {sort} ASC, fact_id ASC LIMIT ?"
        )
        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, (*params, limit + 1)).fetchall()

        return _page(
            rows,
            limit=limit,
            cursor_of=lambda f: (f.valid_from or NULL_SORT_KEY, f.fact_id),
        )

    def close_fact(
        self,
        *,
        deployment_id: str,
        subject: str,
        predicate: str,
        object: str,
        valid_to: Optional[str] = None,
    ) -> bool:
        subject, predicate, obj = _normalized_triple(subject, predicate, object)
        with self._lock:
            conn = self._connect()
            with conn:
                cur = conn.execute(
                    "UPDATE facts SET valid_to = ? WHERE deployment_id = ? AND subject = ? "
                    "AND predicate = ? AND object = ? AND valid_to IS NULL",
                    (valid_to or today_iso(), deployment_id, subject, predicate, obj),
                )
            return cur.rowcount > 0

    def export_facts(self, *, deployment_id: str) -> Iterator[StoredFact]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT {FACT_COLUMNS} FROM facts WHERE deployment_id = ? "
                "ORDER BY recorded_at ASC, fact_id ASC",
                (deployment_id,),
            ).fetchall()
        for row in rows:
            yield _to_fact(row)

    def count_facts(self, *, deployment_id: str) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
        return int(row["n"])

    # ── Decisions ───────────────────────────────────────────────────────

    def put_decisions(
        self,
        *,
        deployment_id: str,
        engineer_id: str,
        decisions: list,
    ) -> PutResult:
        accepted = 0
        reasons: dict = {}

        with self._lock:
            conn = self._connect()
            for raw in decisions:
                decision = replace(raw, deployment_id=deployment_id, engineer_id=engineer_id)
                reason = validate_decision(decision)
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
                try:
                    with conn:
                        conn.execute(
                            f"INSERT INTO decisions ({DECISION_COLUMNS}) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            _decision_row(decision),
                        )
                except sqlite3.IntegrityError:
                    reasons["duplicate_decision_id"] = reasons.get("duplicate_decision_id", 0) + 1
                    continue
                accepted += 1

        return PutResult(accepted=accepted, rejected=sum(reasons.values()), reasons=reasons)

    def get_decisions(self, *, deployment_id: str, query: DecisionQuery) -> DecisionPage:
        clauses = ["deployment_id = ?"]
        params: list = [deployment_id]

        if query.decision_ids is not None:
            if not query.decision_ids:
                return DecisionPage(decisions=[], next_cursor=None)
            placeholders = ", ".join("?" for _ in query.decision_ids)
            clauses.append(f"decision_id IN ({placeholders})")
            params.extend(query.decision_ids)

        if query.cursor:
            recorded_at, decision_id = decode_cursor(query.cursor, expected=2)
            clauses.append("(recorded_at < ? OR (recorded_at = ? AND decision_id < ?))")
            params.extend([recorded_at, recorded_at, decision_id])

        limit = max(1, query.limit)
        sql = (
            f"SELECT {DECISION_COLUMNS} FROM decisions WHERE {' AND '.join(clauses)} "
            "ORDER BY recorded_at DESC, decision_id DESC LIMIT ?"
        )
        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, (*params, limit + 1)).fetchall()

        decisions = [_to_decision(r) for r in rows[:limit]]
        next_cursor = (
            encode_cursor(decisions[-1].recorded_at, decisions[-1].decision_id)
            if len(rows) > limit and decisions
            else None
        )
        return DecisionPage(decisions=decisions, next_cursor=next_cursor)

    def export_decisions(self, *, deployment_id: str):
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT {DECISION_COLUMNS} FROM decisions WHERE deployment_id = ? "
                "ORDER BY recorded_at ASC, decision_id ASC",
                (deployment_id,),
            ).fetchall()
        for row in rows:
            yield _to_decision(row)

    def count_decisions(self, *, deployment_id: str) -> int:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM decisions WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
        return int(row["n"])

    # ── API keys ────────────────────────────────────────────────────────

    def get_api_key(self, *, key_id: str) -> Optional[ApiKeyRecord]:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT key_id, key_hash, deployment_id, engineer_id, created_at, revoked_at "
                "FROM api_keys WHERE key_id = ?",
                (key_id,),
            ).fetchone()
        return _to_key(row) if row else None

    def put_api_key(self, *, record: ApiKeyRecord) -> None:
        with self._lock:
            conn = self._connect()
            with conn:
                conn.execute(
                    "INSERT INTO api_keys "
                    "(key_id, key_hash, deployment_id, engineer_id, created_at, revoked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.key_id,
                        record.key_hash,
                        record.deployment_id,
                        record.engineer_id,
                        record.created_at,
                        record.revoked_at,
                    ),
                )

    def revoke_api_key(self, *, key_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            with conn:
                cur = conn.execute(
                    "UPDATE api_keys SET revoked_at = datetime('now') "
                    "WHERE key_id = ? AND revoked_at IS NULL",
                    (key_id,),
                )
            return cur.rowcount > 0

    def list_api_keys(self, *, deployment_id: str) -> list[ApiKeyRecord]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT key_id, key_hash, deployment_id, engineer_id, created_at, revoked_at "
                "FROM api_keys WHERE deployment_id = ? ORDER BY created_at ASC",
                (deployment_id,),
            ).fetchall()
        return [_to_key(r) for r in rows]

    # ── Health ──────────────────────────────────────────────────────────

    def health(self) -> StoreHealth:
        try:
            with self._lock:
                conn = self._connect()
                conn.execute("SELECT 1").fetchone()
        except StoreClosedError:
            return StoreHealth.unhealthy("store is closed", backend=self.name)
        except sqlite3.Error as exc:
            return StoreHealth.unhealthy(str(exc), backend=self.name)
        return StoreHealth.healthy(backend=self.name, detail=self._path)


# ── Row mapping ─────────────────────────────────────────────────────────


def _to_fact(row) -> StoredFact:
    return StoredFact(
        fact_id=row["fact_id"],
        deployment_id=row["deployment_id"],
        engineer_id=row["engineer_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        schema_version=row["schema_version"],
        recorded_at=row["recorded_at"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        confidence=float(row["confidence"]),
        source_session_id=row["source_session_id"],
        decision_id=row["decision_id"],
    )


def _decision_row(decision: StoredDecision) -> tuple:
    """Flatten a decision into DECISION_COLUMNS order. Lists become JSON text."""
    return (
        decision.decision_id,
        decision.deployment_id,
        decision.engineer_id,
        decision.title,
        decision.chosen,
        decision.rationale,
        decision.schema_version,
        decision.recorded_at,
        json.dumps(list(decision.alternatives_rejected), ensure_ascii=False),
        json.dumps(list(decision.constraints), ensure_ascii=False),
        json.dumps(list(decision.open_questions), ensure_ascii=False),
        decision.decided_on,
        decision.source_session_id,
    )


def _json_list(raw) -> list:
    """Decode a JSON list column, tolerating a row written by hand."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _to_decision(row) -> StoredDecision:
    return StoredDecision(
        decision_id=row["decision_id"],
        deployment_id=row["deployment_id"],
        engineer_id=row["engineer_id"],
        title=row["title"],
        chosen=row["chosen"],
        rationale=row["rationale"],
        schema_version=row["schema_version"],
        recorded_at=row["recorded_at"],
        alternatives_rejected=_json_list(row["alternatives_rejected"]),
        constraints=_json_list(row["constraints_json"]),
        open_questions=_json_list(row["open_questions"]),
        decided_on=row["decided_on"],
        source_session_id=row["source_session_id"],
    )


def _to_key(row) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=row["key_id"],
        key_hash=row["key_hash"],
        deployment_id=row["deployment_id"],
        engineer_id=row["engineer_id"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def _page(rows, *, limit: int, cursor_of) -> FactPage:
    """Turn ``limit + 1`` rows into a page plus a next cursor.

    Fetching one extra row is how we know whether another page exists without
    a second COUNT query.
    """
    facts = [_to_fact(r) for r in rows[:limit]]
    next_cursor = encode_cursor(*cursor_of(facts[-1])) if len(rows) > limit and facts else None
    return FactPage(facts=facts, next_cursor=next_cursor)
