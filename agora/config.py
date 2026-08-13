"""Server configuration, entirely from the environment.

Twelve-factor on purpose: the agora is deployed as a container per team, and
every knob is something an operator sets in ``docker-compose.yml`` or their
orchestrator. There is no config file to mount and no defaults that silently
reach the network.

Secrets (the Postgres DSN) come from the environment only — never from a
committed file. This mirrors RFC 001 §4.2's split: env vars carry secrets,
structure lives in code.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_STORE = "postgres"
DEFAULT_DEPLOYMENT_ID = "default"
DEFAULT_SQLITE_PATH = "agora.sqlite3"
DEFAULT_MAX_BATCH = 100
DEFAULT_MAX_LIMIT = 500
DEFAULT_PAGE_LIMIT = 100
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _coerce_bool(raw: Optional[str], *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _coerce_int(raw: Optional[str], *, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AgoraServerConfig:
    """Immutable server settings.

    Read once at app construction. Reload by building a new app.
    """

    store: str = DEFAULT_STORE
    dsn: Optional[str] = None
    sqlite_path: str = DEFAULT_SQLITE_PATH
    deployment_id: str = DEFAULT_DEPLOYMENT_ID
    auto_migrate: bool = False
    max_batch: int = DEFAULT_MAX_BATCH
    max_limit: int = DEFAULT_MAX_LIMIT
    default_limit: int = DEFAULT_PAGE_LIMIT
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = "info"
    _source: str = field(default="env", repr=False)


def load_config(env: Optional[dict] = None) -> AgoraServerConfig:
    """Build config from ``env`` (defaults to ``os.environ``).

    Takes the mapping as an argument so tests can exercise precedence without
    mutating process state.
    """
    src = os.environ if env is None else env

    return AgoraServerConfig(
        store=src.get("AGORA_STORE") or DEFAULT_STORE,
        dsn=src.get("AGORA_DSN") or None,
        sqlite_path=src.get("AGORA_SQLITE_PATH") or DEFAULT_SQLITE_PATH,
        deployment_id=src.get("AGORA_DEPLOYMENT_ID") or DEFAULT_DEPLOYMENT_ID,
        auto_migrate=_coerce_bool(src.get("AGORA_AUTO_MIGRATE"), default=False),
        max_batch=_coerce_int(src.get("AGORA_MAX_BATCH"), default=DEFAULT_MAX_BATCH),
        max_limit=_coerce_int(src.get("AGORA_MAX_LIMIT"), default=DEFAULT_MAX_LIMIT),
        default_limit=_coerce_int(src.get("AGORA_PAGE_LIMIT"), default=DEFAULT_PAGE_LIMIT),
        host=src.get("AGORA_HOST") or DEFAULT_HOST,
        port=_coerce_int(src.get("AGORA_PORT"), default=DEFAULT_PORT),
        log_level=(src.get("AGORA_LOG_LEVEL") or "info").lower(),
    )
