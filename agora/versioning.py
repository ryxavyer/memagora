"""Wire-format version negotiation.

Engineers upgrade their palace install on their own schedule; the server is
upgraded by whoever operates the deployment. Those two clocks are never in
sync, so every payload carries a ``schema_version`` and the server decides what
to do with it (see ``contracts/README.md``).

Policy, pinned here at v0.3:

* Same major, any minor — accepted. Unknown fields are ignored (pydantic
  models are configured ``extra="ignore"``), which is what lets a newer client
  talk to an older server.
* Older major — looked up in :data:`MIGRATIONS`. Empty today; the table is the
  seam that matters, so a 1.x server can still accept 0.x clients later.
* Newer major — refused with a clear error rather than silently mis-storing
  fields whose meaning has changed.
"""

from typing import Callable, Optional

from contracts import SCHEMA_VERSION

SERVER_SCHEMA_VERSION = SCHEMA_VERSION

# major → callable rewriting an older fact dict into the server's current
# shape. Empty at v0.3: only one major exists.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


class UnsupportedSchemaVersion(ValueError):
    """Raised when a payload's schema version cannot be handled."""


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``"MAJOR.MINOR.PATCH"``. Missing components default to 0."""
    if not isinstance(version, str) or not version.strip():
        raise UnsupportedSchemaVersion("missing schema_version")
    parts = version.strip().split(".")
    try:
        numbers = [int(p) for p in parts[:3]]
    except ValueError as exc:
        raise UnsupportedSchemaVersion(f"malformed schema_version {version!r}") from exc
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def server_major() -> int:
    return parse_version(SERVER_SCHEMA_VERSION)[0]


def check_supported(version: str) -> None:
    """Raise :class:`UnsupportedSchemaVersion` if the server cannot accept it."""
    major = parse_version(version)[0]
    current = server_major()
    if major == current:
        return
    if major < current and major in MIGRATIONS:
        return
    if major < current:
        raise UnsupportedSchemaVersion(
            f"schema_version {version} is older than this server supports "
            f"({SERVER_SCHEMA_VERSION}) and no migration is registered"
        )
    raise UnsupportedSchemaVersion(
        f"schema_version {version} is newer than this server supports "
        f"({SERVER_SCHEMA_VERSION}); upgrade the agora deployment"
    )


def resolve_fact_version(fact_version: Optional[str], envelope_version: str) -> str:
    """Per-fact version wins over the envelope; the envelope wins over nothing.

    A batch can legitimately mix versions when a client buffered facts across
    its own upgrade, so the per-fact field is the authority when present.
    """
    return fact_version or envelope_version


def supported_versions() -> list[str]:
    """Advertised on ``GET /health``."""
    majors = sorted({server_major(), *MIGRATIONS})
    return [f"{m}.x" for m in majors]
