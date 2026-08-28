"""``POST /facts`` and ``GET /facts``."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from ..auth import Principal, require_principal
from ..errors import bad_request, payload_too_large
from ..models import FactOut, GetFactsOut, PostFactsIn, PostFactsOut
from ..storage.base import FactQuery, StoredFact, new_fact_id, utc_now_iso
from ..versioning import UnsupportedSchemaVersion, check_supported, resolve_fact_version

router = APIRouter(tags=["facts"])


@router.post("/facts", response_model=PostFactsOut)
def post_facts(
    body: PostFactsIn,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> PostFactsOut:
    """Ingest a batch of classified facts.

    Partial acceptance — this pins the contract ``contracts/api.py`` left open
    at v0.1. Facts that fail validation or collide with an existing open triple
    are counted in ``rejected``; everything else is stored. The whole batch is
    refused only when the envelope itself is unusable (unsupported schema
    version, too many facts), because in that case the server cannot trust its
    reading of any fact in it.
    """
    config = request.app.state.config
    store = request.app.state.store

    if len(body.facts) > config.max_batch:
        raise payload_too_large(
            f"{len(body.facts)} facts exceeds the server's limit of {config.max_batch}"
        )

    try:
        check_supported(body.schema_version)
    except UnsupportedSchemaVersion as exc:
        raise bad_request("schema_version_unsupported", str(exc)) from exc

    reasons: dict[str, int] = {}
    pending = prepare_facts(
        body.facts,
        envelope_version=body.schema_version,
        principal=principal,
        recorded_at=utc_now_iso(),
        reasons=reasons,
    )

    result = store.put_facts(
        deployment_id=principal.deployment_id,
        engineer_id=principal.engineer_id,
        facts=pending,
    )

    for reason, count in result.reasons.items():
        reasons[reason] = reasons.get(reason, 0) + count
    rejected = sum(reasons.values())

    return PostFactsOut(
        accepted=result.accepted,
        rejected=rejected,
        message=summarize_reasons(reasons) if reasons else None,
    )


@router.get("/facts", response_model=GetFactsOut)
def get_facts(
    request: Request,
    principal: Principal = Depends(require_principal),
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,
    as_of: Optional[str] = Query(None, description="ISO date; interval is inclusive both ends"),
    current: bool = Query(False, description="Only facts with no end bound"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0),
    decision_id: Optional[str] = Query(None, description="Facts produced by one decision"),
    limit: Optional[int] = Query(None, ge=1),
    cursor: Optional[str] = None,
) -> GetFactsOut:
    """Query the deployment's facts, newest ingest first."""
    config = request.app.state.config
    store = request.app.state.store

    query = FactQuery(
        subject=subject,
        predicate=predicate,
        object=object,
        as_of=as_of,
        current_only=current,
        min_confidence=min_confidence,
        decision_id=decision_id,
        limit=min(limit or config.default_limit, config.max_limit),
        cursor=cursor,
    )

    try:
        page = store.get_facts(deployment_id=principal.deployment_id, query=query)
    except ValueError as exc:
        raise bad_request("invalid_cursor", str(exc)) from exc

    return GetFactsOut(
        facts=[FactOut.from_stored(f) for f in page.facts],
        next_cursor=page.next_cursor,
    )


def prepare_facts(
    items,
    *,
    envelope_version: str,
    principal: Principal,
    recorded_at: str,
    reasons: dict,
) -> list[StoredFact]:
    """Turn wire facts into storable ones, counting per-fact version failures.

    Shared by ``POST /facts`` and ``POST /ingest`` so the two paths cannot
    disagree about provenance or version resolution. ``reasons`` is mutated in
    place — the caller merges it with whatever the store reports.
    """
    prepared: list[StoredFact] = []
    for item in items:
        version = resolve_fact_version(item.schema_version, envelope_version)
        try:
            check_supported(version)
        except UnsupportedSchemaVersion:
            reasons["schema_version_unsupported"] = reasons.get("schema_version_unsupported", 0) + 1
            continue
        prepared.append(
            StoredFact(
                fact_id=new_fact_id(),
                # Provenance from the key, never from the body.
                deployment_id=principal.deployment_id,
                engineer_id=principal.engineer_id,
                subject=item.subject,
                predicate=item.predicate,
                object=item.object,
                schema_version=version,
                recorded_at=recorded_at,
                valid_from=item.valid_from,
                valid_to=item.valid_to,
                confidence=item.confidence,
                source_session_id=item.source_session_id,
                decision_id=item.decision_id,
            )
        )
    return prepared


def summarize_reasons(reasons: dict) -> str:
    """Human-readable rejection summary, stable ordering for testability."""
    parts = [f"{reason}: {count}" for reason, count in sorted(reasons.items())]
    return "rejected — " + ", ".join(parts)
