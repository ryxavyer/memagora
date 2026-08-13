"""Storage abstraction for the agora server.

Follows the conventions RFC 001 established for palace-side storage backends
(kwargs-only signatures, ``ClassVar`` metadata, frozen dataclass results, no
driver types crossing the seam) without importing anything from ``mempalace`` —
``agora`` is a separate deployable with its own dependency profile.

Every fact-facing method takes ``deployment_id`` explicitly. A store
implementation is structurally incapable of answering an unscoped query, which
is how "no cross-deployment data leakage by construction" is enforced.

Temporal semantics mirror the inherited palace knowledge graph exactly:

* ``valid_from`` / ``valid_to`` are nullable ISO-8601 **text**, compared
  lexicographically. Partial dates (``"2026-01"``) are legal.
* NULL on either bound means unbounded; ``valid_to IS NULL`` means the fact is
  current.
* The as-of predicate is inclusive on both ends.
* At most one open row per ``(deployment_id, subject, predicate, object)``.
"""

import base64
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Iterator, Optional


# ── Errors ──────────────────────────────────────────────────────────────


class AgoraStoreError(Exception):
    """Base class for every storage error raised by the agora server."""


class StoreClosedError(AgoraStoreError):
    """Raised when a store method is called after ``close()``."""


class MigrationError(AgoraStoreError):
    """Raised when schema migration fails."""


class ConfigurationError(AgoraStoreError):
    """Raised when a store is asked to start without the settings it needs."""


class UnsupportedFilterError(AgoraStoreError):
    """Raised when a query asks for a filter the store cannot honor.

    Silently dropping a filter would return more rows than the caller asked
    for, which for a scoped fact store is a privacy bug rather than a
    performance quirk. Stores MUST raise instead.
    """


# ── Value types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoredFact:
    """A fact as the server persists it: the wire payload plus provenance.

    ``deployment_id`` and ``engineer_id`` are derived from the API key by the
    request layer — never read from a request body.
    """

    fact_id: str
    deployment_id: str
    engineer_id: str
    subject: str
    predicate: str
    object: str
    schema_version: str
    recorded_at: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_session_id: Optional[str] = None

    @property
    def current(self) -> bool:
        """True when the fact has no end bound — i.e. it still holds."""
        return self.valid_to is None


@dataclass(frozen=True)
class FactQuery:
    """Filters for ``get_facts``.

    ``as_of`` selects facts whose validity interval contains the given date,
    inclusive on both ends. ``current_only`` selects facts with no end bound.
    The two are independent and may be combined.
    """

    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    as_of: Optional[str] = None
    current_only: bool = False
    min_confidence: Optional[float] = None
    limit: int = 100
    cursor: Optional[str] = None


@dataclass(frozen=True)
class FactPage:
    """One page of facts plus an opaque cursor for the next."""

    facts: list[StoredFact]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class PutResult:
    """Outcome of a ``put_facts`` batch.

    ``reasons`` maps a rejection reason to a count, so the HTTP layer can
    build a useful ``message`` without the store knowing about HTTP.
    """

    accepted: int
    rejected: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class ApiKeyRecord:
    """A per-engineer API key, scoped to exactly one deployment."""

    key_id: str
    key_hash: str
    deployment_id: str
    engineer_id: str
    created_at: str
    revoked_at: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class StoreHealth:
    """Mirrors the palace-side ``HealthStatus`` shape without importing it."""

    ok: bool
    detail: str = ""
    backend: str = ""
    latency_ms: Optional[float] = None

    @classmethod
    def healthy(cls, *, backend: str = "", detail: str = "", latency_ms=None) -> "StoreHealth":
        return cls(ok=True, detail=detail, backend=backend, latency_ms=latency_ms)

    @classmethod
    def unhealthy(cls, detail: str, *, backend: str = "") -> "StoreHealth":
        return cls(ok=False, detail=detail, backend=backend)


# ── Validation ──────────────────────────────────────────────────────────

MAX_FIELD_LEN = 512
MAX_SESSION_ID_LEN = 200

# ISO-8601 date or partial date: 2026, 2026-01, 2026-01-31. Optionally a full
# timestamp. Lexicographic comparison is only meaningful for zero-padded
# values, so anything else is rejected at the door.
_ISO_DATE_RE = re.compile(
    r"^\d{4}"  # year
    r"(-\d{2}"  # -month
    r"(-\d{2}"  # -day
    r"([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?"  # optional time
    r")?)?$"
)


def validate_fact(fact: StoredFact) -> Optional[str]:
    """Return a rejection reason, or ``None`` when the fact is storable.

    Called by every write path (HTTP ingest and ``agora-admin import``) so a
    fact cannot enter the store through a side door without the same checks.
    """
    for name, value in (
        ("subject", fact.subject),
        ("predicate", fact.predicate),
        ("object", fact.object),
    ):
        if not isinstance(value, str) or not value.strip():
            return f"empty_{name}"
        if len(value) > MAX_FIELD_LEN:
            return f"{name}_too_long"

    for name, value in (("valid_from", fact.valid_from), ("valid_to", fact.valid_to)):
        if value is not None and not _ISO_DATE_RE.match(value):
            return f"malformed_{name}"

    if fact.valid_from and fact.valid_to and fact.valid_from > fact.valid_to:
        return "inverted_validity"

    if not isinstance(fact.confidence, (int, float)) or not 0.0 <= float(fact.confidence) <= 1.0:
        return "confidence_out_of_range"

    if fact.source_session_id is not None and len(fact.source_session_id) > MAX_SESSION_ID_LEN:
        return "source_session_id_too_long"

    if not fact.deployment_id or not fact.engineer_id:
        return "missing_provenance"

    return None


def normalize_fact(fact: StoredFact) -> StoredFact:
    """Trim whitespace on the triple. Predicates are lowercased snake_case.

    Mirrors the palace knowledge graph's normalization so a fact written by
    two engineers who typed it differently still collides on the open-triple
    uniqueness rule.
    """
    return replace(
        fact,
        subject=fact.subject.strip(),
        predicate=fact.predicate.strip().lower().replace(" ", "_"),
        object=fact.object.strip(),
    )


def new_fact_id() -> str:
    return f"f_{uuid.uuid4().hex}"


def utc_now_iso() -> str:
    """Server ingest timestamp. Always UTC, always second-resolution ISO."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── Cursors ─────────────────────────────────────────────────────────────

# Sort key used for facts with no start bound, so timeline ordering can be a
# single COALESCE expression that keyset-paginates in both SQLite and Postgres.
NULL_SORT_KEY = "9999-12-31"


def encode_cursor(*parts: str) -> str:
    """Opaque, URL-safe cursor over a keyset tuple."""
    raw = "\x1f".join(parts).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, expected: int) -> list[str]:
    """Inverse of :func:`encode_cursor`. Raises ``ValueError`` if malformed."""
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"malformed cursor: {exc}") from exc
    parts = raw.split("\x1f")
    if len(parts) != expected:
        raise ValueError("malformed cursor: wrong arity")
    return parts


# ── Shared query construction ───────────────────────────────────────────

FACT_COLUMNS = (
    "fact_id, deployment_id, engineer_id, subject, predicate, object, "
    "schema_version, recorded_at, valid_from, valid_to, confidence, source_session_id"
)


def build_fact_filters(
    *,
    deployment_id: str,
    query: FactQuery,
    ph: str = "?",
) -> tuple[list[str], list]:
    """Build the shared WHERE clauses for ``get_facts``.

    Both stores use this so the deployment scope and the temporal predicate
    are written exactly once. ``ph`` is the driver's placeholder token (``?``
    for sqlite3, ``%s`` for psycopg).

    The as-of predicate is inclusive on both ends and treats NULL bounds as
    unbounded — identical to ``mempalace/knowledge_graph.py``.
    """
    clauses = [f"deployment_id = {ph}"]
    params: list = [deployment_id]

    for column, value in (
        ("subject", query.subject),
        ("predicate", query.predicate),
        ("object", query.object),
    ):
        if value:
            clauses.append(f"{column} = {ph}")
            params.append(value)

    if query.as_of:
        clauses.append(
            f"(valid_from IS NULL OR valid_from <= {ph}) AND (valid_to IS NULL OR valid_to >= {ph})"
        )
        params.extend([query.as_of, query.as_of])

    if query.current_only:
        clauses.append("valid_to IS NULL")

    if query.min_confidence is not None:
        clauses.append(f"confidence >= {ph}")
        params.append(query.min_confidence)

    return clauses, params


# ── Migrations ──────────────────────────────────────────────────────────


def load_migrations(migrations_dir: Path) -> list[tuple[str, str]]:
    """Return ``[(version, sql), …]`` sorted by filename.

    Versions are the numeric filename prefix (``001_init.sql`` → ``001``).
    Stores apply the pending ones in order and record each in
    ``schema_migrations``; the runner is deliberately per-store because
    transaction and DDL semantics differ between drivers.
    """
    if not migrations_dir.is_dir():
        raise MigrationError(f"no migrations directory at {migrations_dir}")
    out = []
    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        out.append((version, path.read_text(encoding="utf-8")))
    if not out:
        raise MigrationError(f"no .sql migrations found in {migrations_dir}")
    return out


def split_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements.

    Line comments are stripped first — prose explaining a schema decision will
    sooner or later contain a semicolon, and that must not be read as a
    statement boundary. Migration files are ours, not user input: plain DDL
    with no string literals, so this stays dependency-free rather than pulling
    in a SQL parser.
    """
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


# ── The store contract ──────────────────────────────────────────────────


class AgoraStore(ABC):
    """Pluggable persistence for facts and API keys.

    Keys live in the same store as facts on purpose: one thing to deploy, one
    thing to back up, one thing to swap. A team replacing Postgres implements
    both method families.

    Lifecycle mirrors RFC 001 §2.6 — ``__init__`` does no I/O, connections are
    established lazily, and after ``close()`` every method raises
    ``StoreClosedError``.
    """

    name: ClassVar[str]
    spec_version: ClassVar[str] = "1.0"
    capabilities: ClassVar[frozenset[str]] = frozenset()

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def from_config(cls, config) -> "AgoraStore":
        """Build a store from an ``AgoraServerConfig``.

        Takes the whole config rather than named kwargs so a third-party store
        can read its own ``AGORA_*`` settings without the registry knowing
        what they are.
        """

    # ── Schema ──────────────────────────────────────────────────────────

    @abstractmethod
    def migrate(self) -> list[str]:
        """Apply pending migrations. Idempotent. Returns versions applied."""

    # ── Facts ───────────────────────────────────────────────────────────

    @abstractmethod
    def put_facts(
        self,
        *,
        deployment_id: str,
        engineer_id: str,
        facts: list[StoredFact],
    ) -> PutResult:
        """Store a batch. Partial acceptance: invalid or duplicate facts are
        counted in ``rejected`` while the rest are stored."""

    @abstractmethod
    def get_facts(self, *, deployment_id: str, query: FactQuery) -> FactPage:
        """Facts matching ``query``, newest ingest first."""

    @abstractmethod
    def timeline(
        self,
        *,
        deployment_id: str,
        subject: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> FactPage:
        """Facts ordered by ``valid_from`` ascending, unbounded starts last."""

    @abstractmethod
    def export_facts(self, *, deployment_id: str) -> Iterator[StoredFact]:
        """Stream every fact for a deployment — the storage-swap migration path."""

    @abstractmethod
    def count_facts(self, *, deployment_id: str) -> int:
        """Total facts stored for a deployment."""

    # ── API keys ────────────────────────────────────────────────────────

    @abstractmethod
    def get_api_key(self, *, key_id: str) -> Optional[ApiKeyRecord]:
        """Look up a key by its public id. Returns ``None`` when unknown."""

    @abstractmethod
    def put_api_key(self, *, record: ApiKeyRecord) -> None:
        """Insert a key record."""

    @abstractmethod
    def revoke_api_key(self, *, key_id: str) -> bool:
        """Mark a key revoked. Returns False when the key does not exist."""

    @abstractmethod
    def list_api_keys(self, *, deployment_id: str) -> list[ApiKeyRecord]:
        """Every key issued for a deployment, revoked ones included."""

    # ── Lifecycle ───────────────────────────────────────────────────────

    @abstractmethod
    def health(self) -> StoreHealth:
        """Round-trip the store cheaply and report."""

    def close(self) -> None:
        """Release handles. Default is a no-op for stateless stores."""
