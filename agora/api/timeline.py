"""``GET /timeline`` — facts ordered by when they started holding.

Mirrors ``KnowledgeGraph.timeline()`` on the palace side: ascending
``valid_from`` with unbounded starts last, and a subject filter that matches
either end of the triple (asking about a service should surface both what it
owns and what depends on it).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from ..auth import Principal, require_principal
from ..errors import bad_request
from ..models import FactOut, GetFactsOut

router = APIRouter(tags=["timeline"])


@router.get("/timeline", response_model=GetFactsOut)
def get_timeline(
    request: Request,
    principal: Principal = Depends(require_principal),
    subject: Optional[str] = Query(None, description="Matches subject or object"),
    limit: Optional[int] = Query(None, ge=1),
    cursor: Optional[str] = None,
) -> GetFactsOut:
    config = request.app.state.config
    store = request.app.state.store

    try:
        page = store.timeline(
            deployment_id=principal.deployment_id,
            subject=subject,
            limit=min(limit or config.default_limit, config.max_limit),
            cursor=cursor,
        )
    except ValueError as exc:
        raise bad_request("invalid_cursor", str(exc)) from exc

    return GetFactsOut(
        facts=[FactOut.from_stored(f) for f in page.facts],
        next_cursor=page.next_cursor,
    )
