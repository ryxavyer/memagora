"""Wire format shared between MemAgora palace clients and the agora server.

Pure dataclass schemas. No runtime dependencies. Independently versioned
from both ``mempalace`` (palace client) and the agora server so rolling
deploys can advance one side at a time.

Public surface:

* ``FactPayload`` — a single classified fact crossing the palace→agora boundary.
* ``DecisionRecord`` — the reasoning that produced a set of facts.
* ``FactClose`` — a request to end a fact that no longer holds.
* ``PostFactsRequest`` / ``PostFactsResponse`` — POST /facts wire shapes.
* ``IngestRequest`` / ``IngestResponse`` — POST /ingest wire shapes (facts + decisions).
* ``GetFactsResponse`` / ``GetDecisionsResponse`` — read wire shapes.
* ``SCHEMA_VERSION`` — current wire format version (semver).
"""

from .facts import DecisionRecord, FactClose, FactPayload, SCHEMA_VERSION
from .api import (
    GetDecisionsResponse,
    GetFactsResponse,
    IngestRequest,
    IngestResponse,
    PostFactsRequest,
    PostFactsResponse,
)

__all__ = [
    "DecisionRecord",
    "FactClose",
    "FactPayload",
    "GetDecisionsResponse",
    "GetFactsResponse",
    "IngestRequest",
    "IngestResponse",
    "PostFactsRequest",
    "PostFactsResponse",
    "SCHEMA_VERSION",
]
