"""``GET /decisions`` and ``GET /decisions/{decision_id}``.

The read side of the "why" question. Facts say what is true; a decision says
why it was chosen, what lost, and what is still open. Query tools on the
palace side reach these two endpoints: find facts about a subject, collect
their ``decision_id``s, fetch the reasoning behind them.

There is no search over rationale text, deliberately. The agora is a knowledge
graph with reasoning attached, not a document store.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from ..auth import Principal, require_principal
from ..errors import api_error, bad_request
from ..models import DecisionOut, GetDecisionsOut
from ..storage.base import DecisionQuery

router = APIRouter(tags=["decisions"])


@router.get("/decisions", response_model=GetDecisionsOut)
def get_decisions(
    request: Request,
    principal: Principal = Depends(require_principal),
    ids: Optional[str] = Query(None, description="Comma-separated decision ids"),
    limit: Optional[int] = Query(None, ge=1),
    cursor: Optional[str] = None,
) -> GetDecisionsOut:
    config = request.app.state.config
    store = request.app.state.store

    decision_ids = None
    if ids is not None:
        # An explicit empty filter means "none", never "all" — the same rule
        # the storage layer enforces.
        decision_ids = [part.strip() for part in ids.split(",") if part.strip()]

    query = DecisionQuery(
        decision_ids=decision_ids,
        limit=min(limit or config.default_limit, config.max_limit),
        cursor=cursor,
    )

    try:
        page = store.get_decisions(deployment_id=principal.deployment_id, query=query)
    except ValueError as exc:
        raise bad_request("invalid_cursor", str(exc)) from exc

    return GetDecisionsOut(
        decisions=[DecisionOut.from_stored(d) for d in page.decisions],
        next_cursor=page.next_cursor,
    )


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(
    decision_id: str,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> DecisionOut:
    store = request.app.state.store
    decision = store.get_decision(deployment_id=principal.deployment_id, decision_id=decision_id)
    if decision is None:
        raise api_error(404, "not_found", f"no decision {decision_id!r} in this deployment")
    return DecisionOut.from_stored(decision)
