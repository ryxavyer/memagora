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

from agora.models import FactIn, FactOut, GetFactsOut, PostFactsIn, PostFactsOut  # noqa: E402
from agora.storage.base import StoredFact, new_fact_id, utc_now_iso  # noqa: E402
from contracts import (  # noqa: E402
    SCHEMA_VERSION,
    FactPayload,
    GetFactsResponse,
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
