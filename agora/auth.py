"""Per-engineer API keys, scoped to exactly one deployment.

The key is the *only* source of ``deployment_id`` and ``engineer_id``. Neither
is ever read from a request body, which is what makes cross-deployment leakage
structurally impossible rather than a validation rule someone can forget.

Key format::

    ak_1a2b3c4d.9f8e7d6c5b4a39281706f5e4d3c2b1a0
    └── key_id ─┘ └────────── secret (128 bits) ──────────┘

The id is stored in the clear and indexed; the secret is stored as a SHA-256
digest. A fast hash is the right choice here — unlike a password, the secret is
128 bits of ``secrets.token_hex`` output, so there is no dictionary to attack
and nothing for a slow KDF to buy. Comparison is constant-time regardless.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from .errors import unauthorized
from .storage.base import ApiKeyRecord, utc_now_iso

KEY_ID_PREFIX = "ak_"
_KEY_ID_BYTES = 4
_SECRET_BYTES = 16


@dataclass(frozen=True)
class Principal:
    """Who is making this request, resolved from the API key."""

    deployment_id: str
    engineer_id: str
    key_id: str


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_key(*, deployment_id: str, engineer_id: str) -> tuple[str, ApiKeyRecord]:
    """Mint a key. Returns ``(full_key, record)``.

    The full key is shown to the operator once and never stored — only the
    record goes to the database.
    """
    key_id = KEY_ID_PREFIX + secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    record = ApiKeyRecord(
        key_id=key_id,
        key_hash=hash_secret(secret),
        deployment_id=deployment_id,
        engineer_id=engineer_id,
        created_at=utc_now_iso(),
    )
    return f"{key_id}.{secret}", record


def parse_key(raw: str) -> Optional[tuple[str, str]]:
    """Split a presented key into ``(key_id, secret)``, or ``None`` if malformed."""
    if not raw or "." not in raw:
        return None
    key_id, _, secret = raw.partition(".")
    if not key_id.startswith(KEY_ID_PREFIX) or not secret:
        return None
    return key_id, secret


def verify_secret(record: ApiKeyRecord, secret: str) -> bool:
    return hmac.compare_digest(record.key_hash, hash_secret(secret))


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def resolve_principal(request: Request) -> Optional[Principal]:
    """Resolve a principal, or ``None`` when the request is not authenticated.

    Used directly by ``GET /health``, which answers either way.
    """
    token = bearer_token(request)
    if not token:
        return None
    parsed = parse_key(token)
    if not parsed:
        return None
    key_id, secret = parsed

    record = request.app.state.store.get_api_key(key_id=key_id)
    if record is None or not record.active or not verify_secret(record, secret):
        return None

    return Principal(
        deployment_id=record.deployment_id,
        engineer_id=record.engineer_id,
        key_id=record.key_id,
    )


def require_principal(request: Request) -> Principal:
    """FastAPI dependency for every endpoint that touches facts.

    Every failure — missing header, malformed key, unknown id, revoked key,
    wrong secret — returns the same 401 with the same message. Distinguishing
    them would tell an attacker which key ids exist.
    """
    principal = resolve_principal(request)
    if principal is None:
        raise unauthorized()
    return principal
