"""The wire format has two definitions; this is what keeps them honest.

``contracts.FactPayload`` is a stdlib dataclass with no dependencies — it is
what engineer-side code and third-party clients import. ``agora.models`` mirrors
it in pydantic for server-side parsing. Nothing enforces that the two agree
except this module, so a field added to one and forgotten in the other fails
here rather than silently dropping data in production.
"""

import dataclasses

import pytest

pytest.importorskip("fastapi", reason="agora server deps not installed")

from agora.models import (  # noqa: E402
    DecisionIn,
    DecisionOut,
    FactCloseIn,
    FactIn,
    FactOut,
    GetDecisionsOut,
    GetFactsOut,
    IngestIn,
    IngestOut,
    PostFactsIn,
    PostFactsOut,
)
from agora.storage.base import (  # noqa: E402
    StoredDecision,
    StoredFact,
    new_fact_id,
    utc_now_iso,
)
from contracts import (  # noqa: E402
    SCHEMA_VERSION,
    DecisionRecord,
    FactClose,
    FactPayload,
    GetDecisionsResponse,
    GetFactsResponse,
    IngestRequest,
    IngestResponse,
    PostFactsRequest,
    PostFactsResponse,
)


def field_names(dataclass_type) -> set:
    return {f.name for f in dataclasses.fields(dataclass_type)}


def test_fact_in_matches_the_contract_exactly():
    assert set(FactIn.model_fields) == field_names(FactPayload)


def test_post_request_matches_the_contract():
    assert set(PostFactsIn.model_fields) == field_names(PostFactsRequest)


def test_post_response_matches_the_contract():
    assert set(PostFactsOut.model_fields) == field_names(PostFactsResponse)


def test_get_response_matches_the_contract():
    assert set(GetFactsOut.model_fields) == field_names(GetFactsResponse)


def test_fact_out_is_a_superset_of_the_contract():
    # Responses may add fields (contracts/api.py tells clients to tolerate
    # unknown ones) but may never drop a contract field.
    extra = set(FactOut.model_fields) - field_names(FactPayload)
    assert field_names(FactPayload) <= set(FactOut.model_fields)
    assert extra == {"fact_id", "engineer_id", "recorded_at", "current"}


def test_stored_fact_covers_every_contract_field():
    assert field_names(FactPayload) <= field_names(StoredFact)


def test_fact_in_round_trips_through_the_contract():
    incoming = FactIn(subject="api", predicate="owned_by", object="platform", confidence=0.8)
    payload = incoming.to_contract()
    assert isinstance(payload, FactPayload)
    assert payload.schema_version == SCHEMA_VERSION
    assert dataclasses.asdict(payload)["subject"] == "api"


def test_fact_in_takes_the_resolved_version():
    incoming = FactIn(subject="s", predicate="p", object="o", schema_version="0.2.0")
    assert incoming.to_contract().schema_version == "0.2.0"
    assert incoming.to_contract(schema_version="0.3.0").schema_version == "0.3.0"


def test_fact_out_from_stored_preserves_every_value():
    stored = StoredFact(
        fact_id=new_fact_id(),
        deployment_id="team",
        engineer_id="alice",
        subject="api",
        predicate="owned_by",
        object="platform",
        schema_version="0.1.0",
        recorded_at=utc_now_iso(),
        valid_from="2026-01-01",
        confidence=0.75,
        source_session_id="sess-1",
    )
    out = FactOut.from_stored(stored)
    for field in field_names(FactPayload):
        assert getattr(out, field) == getattr(stored, field)
    assert out.current is True


def test_deployment_id_is_not_on_the_wire():
    # It comes from the API key. A client cannot name its own deployment, and
    # the response does not echo one back.
    assert "deployment_id" not in FactIn.model_fields
    assert "deployment_id" not in FactOut.model_fields


# ── Decisions (0.2.0) ───────────────────────────────────────────────────


def test_decision_in_matches_the_contract_exactly():
    assert set(DecisionIn.model_fields) == field_names(DecisionRecord)


def test_ingest_request_matches_the_contract():
    assert set(IngestIn.model_fields) == field_names(IngestRequest)


def test_ingest_response_matches_the_contract():
    assert set(IngestOut.model_fields) == field_names(IngestResponse)


def test_get_decisions_response_matches_the_contract():
    assert set(GetDecisionsOut.model_fields) == field_names(GetDecisionsResponse)


def test_decision_out_is_a_superset_of_the_contract():
    extra = set(DecisionOut.model_fields) - field_names(DecisionRecord)
    assert field_names(DecisionRecord) <= set(DecisionOut.model_fields)
    assert extra == {"engineer_id", "recorded_at"}


def test_stored_decision_covers_every_contract_field():
    assert field_names(DecisionRecord) <= field_names(StoredDecision)


def test_decision_in_round_trips_through_the_contract():
    incoming = DecisionIn(
        decision_id="d_1",
        title="Queue choice",
        chosen="SQS FIFO",
        rationale="Ordering is required.",
        constraints=["Stay on AWS"],
    )
    record = incoming.to_contract()
    assert isinstance(record, DecisionRecord)
    assert record.constraints == ["Stay on AWS"]
    assert record.schema_version == SCHEMA_VERSION


def test_decision_out_from_stored_preserves_every_value():
    stored = StoredDecision(
        decision_id="d_1",
        deployment_id="team",
        engineer_id="alice",
        title="Queue choice",
        chosen="SQS FIFO",
        rationale="Ordering is required.",
        schema_version=SCHEMA_VERSION,
        recorded_at=utc_now_iso(),
        alternatives_rejected=["Kafka"],
        constraints=["Stay on AWS"],
        open_questions=["DLQ?"],
        decided_on="2026-08-01",
        source_session_id="sess-1",
    )
    out = DecisionOut.from_stored(stored)
    for field in field_names(DecisionRecord):
        assert getattr(out, field) == getattr(stored, field)


def test_deployment_id_is_not_on_the_decision_wire():
    assert "deployment_id" not in DecisionIn.model_fields
    assert "deployment_id" not in DecisionOut.model_fields


def test_facts_carry_the_decision_link_in_both_directions():
    assert "decision_id" in FactIn.model_fields
    assert "decision_id" in FactOut.model_fields
    assert "decision_id" in field_names(FactPayload)


def test_fact_close_matches_the_contract_exactly():
    assert set(FactCloseIn.model_fields) == field_names(FactClose)


def test_fact_close_round_trips_through_the_contract():
    closing = FactCloseIn(subject="api", predicate="uses", object="SQS", valid_to="2026-09-01")
    record = closing.to_contract()
    assert isinstance(record, FactClose)
    assert record.valid_to == "2026-09-01"
    assert record.schema_version == SCHEMA_VERSION


def test_ingest_request_still_matches_after_gaining_closes():
    assert set(IngestIn.model_fields) == field_names(IngestRequest)
    assert "closes" in IngestIn.model_fields


def test_ingest_response_reports_closures():
    assert set(IngestOut.model_fields) == field_names(IngestResponse)
    assert "facts_closed" in IngestOut.model_fields
