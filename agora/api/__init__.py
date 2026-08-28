"""HTTP endpoints. Each module owns one resource and no storage details."""

from .decisions import router as decisions_router
from .facts import router as facts_router
from .health import router as health_router
from .ingest import router as ingest_router
from .timeline import router as timeline_router

__all__ = [
    "decisions_router",
    "facts_router",
    "health_router",
    "ingest_router",
    "timeline_router",
]
