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
from dataclasses import dataclass, field, replace
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
    decision_id: Optional[str] = None

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
    decision_id: Optional[str] = None
    limit: int = 100
    cursor: Optional[str] = None


@dataclass(frozen=True)
class FactPage:
    """One page of facts plus an opaque cursor for the next."""

    facts: list[StoredFact]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class StoredDecision:
    """A decision as the server persists it: the wire record plus provenance.

    The list fields are stored as JSON text and always read back whole — they
    are narrative, not query surface.
    """

    decision_id: str
    deployment_id: str
    engineer_id: str
    title: str
    chosen: str
    rationale: str
    schema_version: str
    recorded_at: str
    alternatives_rejected: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    decided_on: Optional[str] = None
    source_session_id: Optional[str] = None


@dataclass(frozen=True)
class DecisionQuery:
    """Filters for ``get_decisions``.

    ``decision_ids`` is how the read path works in practice: find facts about
    a subject, collect their ``decision_id``s, fetch those decisions. There is
    no full-text search over rationale, deliberately — that would make the
    agora a document store.
    """

    decision_ids: Optional[list[str]] = None
    limit: int = 100
    cursor: Optional[str] = None


@dataclass(frozen=True)
class DecisionPage:
    """One page of decisions plus an opaque cursor for the next."""

    decisions: list[StoredDecision]
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
MAX_ID_LEN = 128
# Prose fields (rationale, and each list entry) get a larger cap than a triple
# field but still a hard one — see validate_decision.
MAX_PROSE_LEN = 4000
MAX_LIST_ITEMS = 20

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


def validate_decision(decision: StoredDecision) -> Optional[str]:
    """Return a rejection reason, or ``None`` when the decision is storable.

    The length caps are the privacy boundary made enforceable. A decision
    carries agent-authored prose, which is closer to raw content than a triple
    is; capping it means a misbehaving client cannot quietly turn the agora
    into a transcript store, whatever its prompt says.
    """
    for name, value in (
        ("decision_id", decision.decision_id),
        ("title", decision.title),
        ("chosen", decision.chosen),
        ("rationale", decision.rationale),
    ):
        if not isinstance(value, str) or not value.strip():
            return f"empty_{name}"

    if len(decision.decision_id) > MAX_ID_LEN:
        return "decision_id_too_long"
    for name, value in (("title", decision.title), ("chosen", decision.chosen)):
        if len(value) > MAX_FIELD_LEN:
            return f"{name}_too_long"
    if len(decision.rationale) > MAX_PROSE_LEN:
        return "rationale_too_long"

    for name, values in (
        ("alternatives_rejected", decision.alternatives_rejected),
        ("constraints", decision.constraints),
        ("open_questions", decision.open_questions),
    ):
        if not isinstance(values, (list, tuple)):
            return f"malformed_{name}"
        if len(values) > MAX_LIST_ITEMS:
            return f"{name}_too_many"
        for item in values:
            if not isinstance(item, str):
                return f"malformed_{name}"
            if len(item) > MAX_PROSE_LEN:
                return f"{name}_too_long"

    if decision.decided_on is not None and not _ISO_DATE_RE.match(decision.decided_on):
        return "malformed_decided_on"

    if (
        decision.source_session_id is not None
        and len(decision.source_session_id) > MAX_SESSION_ID_LEN
    ):
        return "source_session_id_too_long"

    if not decision.deployment_id or not decision.engineer_id:
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


def today_iso() -> str:
    """Default end date for a close, matching the palace KG's ``invalidate``."""
    return datetime.now(timezone.utc).date().isoformat()


def _normalized_triple(subject: str, predicate: str, obj: str) -> tuple:
    """Apply write-path normalization to a triple named by a client.

    Without this a close for ``("api", "Owned By", "team")`` would find nothing,
    because the stored row says ``owned_by``.
    """
    return (
        str(subject).strip(),
        str(predicate).strip().lower().replace(" ", "_"),
        str(obj).strip(),
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
    "schema_version, recorded_at, valid_from, valid_to, confidence, source_session_id, "
    "decision_id"
)

DECISION_COLUMNS = (
    "decision_id, deployment_id, engineer_id, title, chosen, rationale, "
    "schema_version, recorded_at, alternatives_rejected, constraints_json, "
    "open_questions, decided_on, source_session_id"
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

    if query.decision_id:
        clauses.append(f"decision_id = {ph}")
        params.append(query.decision_id)

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
    #: Directory of numbered ``.sql`` files this store applies. Set by
    #: concrete stores; read by :meth:`pending_migrations`.
    migrations_dir: ClassVar[Optional[Path]] = None

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

    @abstractmethod
    def applied_migrations(self) -> list[str]:
        """Versions already recorded in ``schema_migrations``.

        Returns ``[]`` when the schema has never been migrated — a store must
        answer this on an empty database rather than raising, because the
        first thing the server does is ask.
        """

    def pending_migrations(self) -> list[str]:
        """Versions on disk that this database has not applied yet.

        The server checks this at startup: running new code against an old
        schema produces confusing per-request failures, and an operator would
        much rather find out when the container comes up.
        """
        if self.migrations_dir is None:
            return []
        applied = set(self.applied_migrations())
        return [
            version for version, _ in load_migrations(self.migrations_dir) if version not in applied
        ]

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
    def close_fact(
        self,
        *,
        deployment_id: str,
        subject: str,
        predicate: str,
        object: str,
        valid_to: Optional[str] = None,
    ) -> bool:
        """End the open fact matching this triple. Returns False if none was open.

        Sets ``valid_to`` rather than deleting the row, so "this was true until
        September" stays answerable. Mirrors ``KnowledgeGraph.invalidate`` on
        the palace side, including its default of today's date.

        Only the open row is touched: closing a triple twice is a no-op that
        reports False, never a rewrite of history that was already closed.
        """

    @abstractmethod
    def export_facts(self, *, deployment_id: str) -> Iterator[StoredFact]:
        """Stream every fact for a deployment — the storage-swap migration path."""

    @abstractmethod
    def count_facts(self, *, deployment_id: str) -> int:
        """Total facts stored for a deployment."""

    # ── Decisions ───────────────────────────────────────────────────────

    @abstractmethod
    def put_decisions(
        self,
        *,
        deployment_id: str,
        engineer_id: str,
        decisions: list[StoredDecision],
    ) -> PutResult:
        """Store a batch of decisions. Partial acceptance, like ``put_facts``.

        A ``decision_id`` that already exists in the deployment is rejected
        rather than overwritten: a decision is a record of what was decided at
        a moment, and silently rewriting one would make the agora's history
        unreliable in exactly the way it exists to prevent.
        """

    @abstractmethod
    def get_decisions(self, *, deployment_id: str, query: DecisionQuery) -> DecisionPage:
        """Decisions matching ``query``, newest ingest first."""

    @abstractmethod
    def export_decisions(self, *, deployment_id: str) -> Iterator[StoredDecision]:
        """Stream every decision for a deployment — the storage-swap path."""

    @abstractmethod
    def count_decisions(self, *, deployment_id: str) -> int:
        """Total decisions stored for a deployment."""

    def get_decision(self, *, deployment_id: str, decision_id: str) -> Optional[StoredDecision]:
        """Fetch one decision by id.

        Concrete, not abstract: it is ``get_decisions`` with a single id, and
        every backend would write the same three lines. Backends that can do
        better may still override it.
        """
        page = self.get_decisions(
            deployment_id=deployment_id,
            query=DecisionQuery(decision_ids=[decision_id], limit=1),
        )
        return page.decisions[0] if page.decisions else None

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
