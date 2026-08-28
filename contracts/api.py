"""HTTP API request/response shapes.

``POST /facts`` (0.1.0) carries facts only and is preserved unchanged so a
palace client that predates decisions keeps working against a newer server.
``POST /ingest`` (0.2.0) is the path agent-driven emission uses: one batch
carrying facts, decisions, or both, so a decision and the facts it produced
land in a single request rather than racing each other.

Clients should be tolerant of unknown fields when deserializing — that
tolerance is what lets a client one release ahead talk to an older server.
"""

from dataclasses import dataclass, field
from typing import Optional

from .facts import DecisionRecord, FactClose, FactPayload, SCHEMA_VERSION


@dataclass(frozen=True)
class PostFactsRequest:
    """Body of POST /facts.

    A batch of one or more classified facts. The agora accepts partially:
    facts that fail validation or duplicate an existing open triple are
    counted as rejected while the rest are stored. The batch as a whole is
    refused only when the envelope itself is unusable.
    """

    facts: list[FactPayload]
    schema_version: str = field(default=SCHEMA_VERSION)


@dataclass(frozen=True)
class PostFactsResponse:
    """Response from POST /facts.

    Fields:
        accepted: Number of facts the server stored.
        rejected: Number of facts rejected (validation, dedup, or auth).
        message:  Optional human-readable status string.
    """

    accepted: int
    rejected: int
    message: Optional[str] = None


@dataclass(frozen=True)
class IngestRequest:
    """Body of POST /ingest — a mixed batch (0.2.0).

    Order within a batch is fixed and matters: decisions, then closes, then
    facts. A fact carrying ``decision_id`` never lands referring to a decision
    that is not there yet, and a replacement fact never collides with the open
    row it supersedes.
    """

    facts: list[FactPayload] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    closes: list[FactClose] = field(default_factory=list)
    schema_version: str = field(default=SCHEMA_VERSION)


@dataclass(frozen=True)
class IngestResponse:
    """Response from POST /ingest.

    Facts and decisions are counted separately: an agent that recorded a
    decision and five facts needs to know which half the server kept.
    """

    facts_accepted: int
    facts_rejected: int
    decisions_accepted: int
    decisions_rejected: int
    facts_closed: int = 0
    message: Optional[str] = None


@dataclass(frozen=True)
class GetFactsResponse:
    """Response from GET /facts.

    Fields:
        facts: Matching facts. Order is implementation-defined; clients
               that need a particular ordering must sort client-side.
        next_cursor: Opaque pagination token; absent when no more pages.
    """

    facts: list[FactPayload]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class GetDecisionsResponse:
    """Response from GET /decisions (0.2.0)."""

    decisions: list[DecisionRecord]
    next_cursor: Optional[str] = None
