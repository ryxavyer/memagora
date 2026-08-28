"""``POST /ingest`` — the mixed batch agent-driven emission uses.

An agent that just made a decision has three things to record: the decision,
the facts it produced, and the facts it *replaced*. Sending them as one batch
fixes the order — decisions, then closes, then facts — so a fact carrying
``decision_id`` never lands pointing at something that is not there yet, and a
replacement never collides with the open row it supersedes.

``POST /facts`` still exists and is unchanged. A palace client that predates
decisions keeps working against this server.
"""

from fastapi import APIRouter, Depends, Request

from ..auth import Principal, require_principal
from ..errors import bad_request, payload_too_large
from ..models import IngestIn, IngestOut
from ..storage.base import StoredDecision, utc_now_iso
from ..versioning import UnsupportedSchemaVersion, check_supported, resolve_fact_version
from .facts import prepare_facts, summarize_reasons

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestOut)
def ingest(
    body: IngestIn,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> IngestOut:
    """Store decisions and facts in one request.

    Partial acceptance on both halves, counted separately: an agent that
    recorded a decision and five facts needs to know which half was kept.
    """
    config = request.app.state.config
    store = request.app.state.store

    total = len(body.facts) + len(body.decisions) + len(body.closes)
    if total > config.max_batch:
        raise payload_too_large(f"{total} items exceeds the server's limit of {config.max_batch}")

    try:
        check_supported(body.schema_version)
    except UnsupportedSchemaVersion as exc:
        raise bad_request("schema_version_unsupported", str(exc)) from exc

    recorded_at = utc_now_iso()
    reasons: dict = {}

    # Decisions first — a fact may reference one in the same batch.
    decision_reasons: dict = {}
    decisions = _prepare_decisions(
        body.decisions,
        envelope_version=body.schema_version,
        principal=principal,
        recorded_at=recorded_at,
        reasons=decision_reasons,
    )
    decision_result = store.put_decisions(
        deployment_id=principal.deployment_id,
        engineer_id=principal.engineer_id,
        decisions=decisions,
    )
    for reason, count in decision_result.reasons.items():
        decision_reasons[reason] = decision_reasons.get(reason, 0) + count

    # Closes run before the new facts: superseding "uses SQS" with "uses
    # Kinesis" is two rows, and closing first is what keeps the old one from
    # sitting there open alongside its own replacement.
    closed = 0
    for item in body.closes:
        if store.close_fact(
            deployment_id=principal.deployment_id,
            subject=item.subject,
            predicate=item.predicate,
            object=item.object,
            valid_to=item.valid_to,
        ):
            closed += 1
        else:
            reasons["close_matched_nothing"] = reasons.get("close_matched_nothing", 0) + 1

    facts = prepare_facts(
        body.facts,
        envelope_version=body.schema_version,
        principal=principal,
        recorded_at=recorded_at,
        reasons=reasons,
    )
    fact_result = store.put_facts(
        deployment_id=principal.deployment_id,
        engineer_id=principal.engineer_id,
        facts=facts,
    )
    for reason, count in fact_result.reasons.items():
        reasons[reason] = reasons.get(reason, 0) + count

    combined = dict(reasons)
    for reason, count in decision_reasons.items():
        combined[reason] = combined.get(reason, 0) + count

    # A close that matched nothing is reported, not counted as a rejected fact.
    fact_rejections = sum(
        count for reason, count in reasons.items() if reason != "close_matched_nothing"
    )

    return IngestOut(
        facts_accepted=fact_result.accepted,
        facts_rejected=fact_rejections,
        facts_closed=closed,
        decisions_accepted=decision_result.accepted,
        decisions_rejected=sum(decision_reasons.values()),
        message=summarize_reasons(combined) if combined else None,
    )


def _prepare_decisions(
    items,
    *,
    envelope_version: str,
    principal: Principal,
    recorded_at: str,
    reasons: dict,
) -> list:
    """Turn wire decisions into storable ones, as ``prepare_facts`` does."""
    prepared = []
    for item in items:
        version = resolve_fact_version(item.schema_version, envelope_version)
        try:
            check_supported(version)
        except UnsupportedSchemaVersion:
            reasons["schema_version_unsupported"] = reasons.get("schema_version_unsupported", 0) + 1
            continue
        prepared.append(
            StoredDecision(
                decision_id=item.decision_id,
                # Provenance from the key, never from the body.
                deployment_id=principal.deployment_id,
                engineer_id=principal.engineer_id,
                title=item.title,
                chosen=item.chosen,
                rationale=item.rationale,
                schema_version=version,
                recorded_at=recorded_at,
                alternatives_rejected=list(item.alternatives_rejected),
                constraints=list(item.constraints),
                open_questions=list(item.open_questions),
                decided_on=item.decided_on,
                source_session_id=item.source_session_id,
            )
        )
    return prepared
