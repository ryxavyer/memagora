"""``GET /health`` — the only endpoint that answers without a key.

Container healthchecks and load balancers need liveness without credentials,
but an unauthenticated caller is told nothing about the deployment: no id, no
counts, no store detail. Present a valid key and the same endpoint becomes an
operator's status page.
"""

from fastapi import APIRouter, Request

from .. import __version__
from ..auth import resolve_principal
from ..models import HealthOut
from ..versioning import supported_versions

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, response_model_exclude_none=True)
def health(request: Request) -> HealthOut:
    store = request.app.state.store
    principal = resolve_principal(request)

    if principal is None:
        return HealthOut(
            status="ok",
            version=__version__,
            schema_versions=supported_versions(),
        )

    store_health = store.health()
    return HealthOut(
        status="ok" if store_health.ok else "degraded",
        version=__version__,
        schema_versions=supported_versions(),
        store=store_health.backend,
        store_ok=store_health.ok,
        store_detail=store_health.detail,
        deployment_id=principal.deployment_id,
        fact_count=store.count_facts(deployment_id=principal.deployment_id),
    )
