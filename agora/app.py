"""FastAPI application factory.

``create_app`` takes both the config and the store as optional arguments so
tests can inject a SQLite store without touching the environment, and so a
future embedding of the agora in a larger service has a seam to build on.

Nothing here reaches the network on import: the store connects lazily on first
use, and migrations run at startup only when ``AGORA_AUTO_MIGRATE`` is set.
"""

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Starlette's base class, not FastAPI's subclass: the router raises the former
# for 404 / 405, and a handler registered on the subclass would miss them.
from starlette.exceptions import HTTPException

from . import __version__
from .api import (
    decisions_router,
    facts_router,
    health_router,
    ingest_router,
    timeline_router,
)
from .config import AgoraServerConfig, load_config
from .storage import AgoraStore, build_store
from .storage.base import AgoraStoreError, MigrationError
from .versioning import SERVER_SCHEMA_VERSION

logger = logging.getLogger(__name__)

SCHEMA_VERSION_HEADER = "X-Agora-Schema-Version"


def create_app(
    *,
    config: Optional[AgoraServerConfig] = None,
    store: Optional[AgoraStore] = None,
) -> FastAPI:
    config = config or load_config()
    store = store or build_store(config)

    app = FastAPI(
        title="MemAgora",
        version=__version__,
        description=(
            "Team knowledge graph. Engineers' palaces POST classified facts here; "
            "raw conversation content never crosses this boundary."
        ),
    )
    app.state.config = config
    app.state.store = store

    if config.auto_migrate:
        applied = store.migrate()
        if applied:
            logger.info("applied migrations: %s", ", ".join(applied))
    else:
        # Refuse to serve against a schema this build does not match. Running
        # new code on an old schema fails per-request, deep in a driver error,
        # long after the operator has moved on; failing here says exactly what
        # to do while they are still watching the deploy.
        pending = store.pending_migrations()
        if pending:
            raise MigrationError(
                "database schema is behind this server: migration(s) "
                f"{', '.join(pending)} not applied. Run `agora-admin migrate` "
                "before starting the server (or set AGORA_AUTO_MIGRATE=1)."
            )

    app.include_router(health_router)
    app.include_router(facts_router)
    app.include_router(ingest_router)
    app.include_router(decisions_router)
    app.include_router(timeline_router)

    @app.middleware("http")
    async def stamp_schema_version(request: Request, call_next):
        response = await call_next(request)
        response.headers[SCHEMA_VERSION_HEADER] = SERVER_SCHEMA_VERSION
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Endpoints raise api_error() with a dict detail; anything else (404s
        # from the router, say) gets wrapped into the same shape.
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = {"error": _code_for(exc.status_code), "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_request", "message": _first_validation_message(exc)},
        )

    @app.exception_handler(AgoraStoreError)
    async def store_error_handler(request: Request, exc: AgoraStoreError):
        # Storage failures are the operator's problem, not the client's — log
        # the detail, return a generic message rather than leaking a DSN.
        logger.exception("storage error")
        return JSONResponse(
            status_code=503,
            content={"error": "storage_unavailable", "message": "the fact store is unavailable"},
        )

    return app


def _code_for(status_code: int) -> str:
    return {
        401: "unauthorized",
        404: "not_found",
        405: "method_not_allowed",
        413: "batch_too_large",
    }.get(status_code, "error")


def _first_validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "request validation failed"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    return f"{location or 'body'}: {first.get('msg', 'invalid')}"
