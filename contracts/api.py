"""HTTP API request/response shapes.

These are stubs for v0.1. The server itself is built in v0.3 and may
introduce additional fields; clients should be tolerant of unknown
fields when deserializing.
"""

from dataclasses import dataclass, field
from typing import Optional

from .facts import FactPayload, SCHEMA_VERSION


@dataclass(frozen=True)
class PostFactsRequest:
    """Body of POST /facts.

    A batch of one or more classified facts. The agora MAY reject the
    entire batch on validation failure or accept a partial batch and
    return per-fact status. v0.3 will pin that contract.
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
class GetFactsResponse:
    """Response from GET /facts.

    Fields:
        facts: Matching facts. Order is implementation-defined; clients
               that need a particular ordering must sort client-side.
        next_cursor: Opaque pagination token; absent when no more pages.
    """

    facts: list[FactPayload]
    next_cursor: Optional[str] = None
