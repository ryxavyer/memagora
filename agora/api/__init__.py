"""HTTP endpoints. Each module owns one resource and no storage details."""

from .facts import router as facts_router
from .health import router as health_router
from .timeline import router as timeline_router

__all__ = ["facts_router", "health_router", "timeline_router"]
