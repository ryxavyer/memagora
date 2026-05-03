"""Wire format shared between MemAgora palace clients and the agora server.

Pure dataclass schemas. No runtime dependencies. Independently versioned
from both ``mempalace`` (palace client) and the agora server so rolling
deploys can advance one side at a time.

Public surface:

* ``FactPayload`` — a single classified fact crossing the palace→agora boundary.
* ``PostFactsRequest`` / ``PostFactsResponse`` — POST /facts wire shapes.
* ``GetFactsResponse`` — GET /facts wire shape.
* ``SCHEMA_VERSION`` — current wire format version (semver).
"""

from .facts import FactPayload, SCHEMA_VERSION
from .api import (
    GetFactsResponse,
    PostFactsRequest,
    PostFactsResponse,
)

__all__ = [
    "FactPayload",
    "GetFactsResponse",
    "PostFactsRequest",
    "PostFactsResponse",
    "SCHEMA_VERSION",
]
