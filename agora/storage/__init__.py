"""Store registry + entry-point discovery.

Mirrors ``mempalace/backends/registry.py`` (RFC 001 §3) on the server side, so
swapping the agora's persistence is a ``pip install`` rather than a fork::

    # pyproject.toml of memagora-store-mysql
    [project.entry-points."agora.stores"]
    mysql = "memagora_store_mysql:MySQLStore"

In-tree stores register explicitly at import time; explicit registration wins
over entry-point discovery on name conflict. ``AGORA_STORE`` selects the
instance — the server-side analogue of ``MEMPALACE_BACKEND``.
"""

import logging
from importlib import metadata
from threading import Lock
from typing import Type

from .base import (
    AgoraStore,
    AgoraStoreError,
    ApiKeyRecord,
    ConfigurationError,
    FactPage,
    FactQuery,
    MigrationError,
    PutResult,
    StoreClosedError,
    StoredFact,
    StoreHealth,
    UnsupportedFilterError,
)

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "agora.stores"

_registry: dict[str, Type[AgoraStore]] = {}
_explicit: set[str] = set()
_discovered = False
_lock = Lock()


def register(name: str, store_cls: Type[AgoraStore]) -> None:
    """Register ``store_cls`` under ``name``. Explicit registration wins."""
    with _lock:
        _registry[name] = store_cls
        _explicit.add(name)


def unregister(name: str) -> None:
    """Remove a registration (primarily for tests)."""
    with _lock:
        _registry.pop(name, None)
        _explicit.discard(name)


def _discover_entry_points() -> None:
    global _discovered
    if _discovered:
        return
    with _lock:
        if _discovered:
            return
        try:
            eps = metadata.entry_points()
            # Py ≥ 3.10 returns an EntryPoints object; older versions a dict.
            # The server itself requires 3.11, but the storage layer is stdlib
            # only and is exercised by the palace-side test matrix down to 3.9.
            group = (
                eps.select(group=_ENTRY_POINT_GROUP)
                if hasattr(eps, "select")
                else eps.get(_ENTRY_POINT_GROUP, [])
            )
        except Exception:
            logger.exception("entry-point discovery for %s failed", _ENTRY_POINT_GROUP)
            group = []
        for ep in group:
            if ep.name in _explicit:
                continue
            try:
                cls = ep.load()
            except Exception:
                logger.exception("failed to load store entry point %r", ep.name)
                continue
            if not isinstance(cls, type) or not issubclass(cls, AgoraStore):
                logger.warning(
                    "entry point %r did not resolve to an AgoraStore subclass (got %r)",
                    ep.name,
                    cls,
                )
                continue
            _registry.setdefault(ep.name, cls)
        _discovered = True


def available_stores() -> list[str]:
    _discover_entry_points()
    return sorted(_registry)


def get_store_class(name: str) -> Type[AgoraStore]:
    """Resolve a store class by name, or raise ``AgoraStoreError``."""
    _discover_entry_points()
    cls = _registry.get(name)
    if cls is None:
        raise AgoraStoreError(f"unknown store {name!r}; available: {', '.join(available_stores())}")
    return cls


def build_store(config) -> AgoraStore:
    """Instantiate the store named by ``config.store``.

    Kept here rather than in ``agora.config`` so the config module stays
    import-light and free of driver imports.
    """
    cls = get_store_class(config.store)
    return cls.from_config(config)


def _register_builtin_stores() -> None:
    """Register in-tree stores, skipping any whose driver is not installed.

    A deployment that only uses SQLite should not need psycopg present, and a
    container that only talks to Postgres should still import cleanly.
    """
    from .sqlite import SQLiteStore

    register(SQLiteStore.name, SQLiteStore)

    try:
        from .postgres import PostgresStore
    except ImportError:  # psycopg not installed
        logger.debug("psycopg not available; postgres store not registered")
    else:
        register(PostgresStore.name, PostgresStore)


_register_builtin_stores()

__all__ = [
    "AgoraStore",
    "AgoraStoreError",
    "ApiKeyRecord",
    "ConfigurationError",
    "FactPage",
    "FactQuery",
    "MigrationError",
    "PutResult",
    "StoreClosedError",
    "StoreHealth",
    "StoredFact",
    "UnsupportedFilterError",
    "available_stores",
    "build_store",
    "get_store_class",
    "register",
    "unregister",
]
