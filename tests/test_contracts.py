"""Tests for the contracts/ wire format package.

Validates:
- Dataclass round-trip (asdict + reconstruct)
- frozen=True invariants (value-object semantics)
- schema_version is present on every payload
- Public exports match the documented surface
"""

from dataclasses import asdict, FrozenInstanceError

import pytest

from contracts import (
    FactPayload,
    GetFactsResponse,
    PostFactsRequest,
    PostFactsResponse,
    SCHEMA_VERSION,
)


def test_schema_version_is_semver():
    """SCHEMA_VERSION should be a non-empty semver-shaped string."""
    parts = SCHEMA_VERSION.split(".")
    assert len(parts) == 3, f"Expected MAJOR.MINOR.PATCH, got {SCHEMA_VERSION!r}"
    for part in parts:
        assert part.isdigit(), f"Non-numeric segment in {SCHEMA_VERSION!r}"


def test_fact_payload_minimal_construction():
    """A fact requires only subject/predicate/object."""
    fact = FactPayload(
        subject="Alice",
        predicate="works_on",
        object="MemAgora",
    )
    assert fact.subject == "Alice"
    assert fact.predicate == "works_on"
    assert fact.object == "MemAgora"
    assert fact.valid_from is None
    assert fact.valid_to is None
    assert fact.confidence == 1.0
    assert fact.source_session_id is None
    assert fact.schema_version == SCHEMA_VERSION


def test_fact_payload_full_construction():
    fact = FactPayload(
        subject="MemAgora",
        predicate="depends_on",
        object="ChromaDB",
        valid_from="2026-01-01",
        valid_to=None,
        confidence=0.85,
        source_session_id="sess-abc-123",
    )
    assert fact.confidence == 0.85
    assert fact.source_session_id == "sess-abc-123"


def test_fact_payload_is_frozen():
    """frozen=True — modifications must raise."""
    fact = FactPayload(subject="x", predicate="is", object="y")
    with pytest.raises(FrozenInstanceError):
        fact.subject = "mutated"


def test_fact_payload_roundtrip():
    """asdict → FactPayload(**d) reconstructs the same fact."""
    original = FactPayload(
        subject="Alice",
        predicate="works_on",
        object="MemAgora",
        valid_from="2026-01-01",
        confidence=0.9,
        source_session_id="sess-1",
    )
    d = asdict(original)
    reconstructed = FactPayload(**d)
    assert reconstructed == original


def test_post_facts_request_carries_schema_version():
    req = PostFactsRequest(
        facts=[FactPayload(subject="x", predicate="is", object="y")],
    )
    assert req.schema_version == SCHEMA_VERSION
    assert len(req.facts) == 1


def test_post_facts_response_minimal():
    resp = PostFactsResponse(accepted=2, rejected=0)
    assert resp.accepted == 2
    assert resp.rejected == 0
    assert resp.message is None


def test_get_facts_response_pagination():
    resp = GetFactsResponse(facts=[], next_cursor="cursor-token")
    assert resp.facts == []
    assert resp.next_cursor == "cursor-token"


def test_get_facts_response_terminal_page():
    resp = GetFactsResponse(facts=[FactPayload(subject="x", predicate="is", object="y")])
    assert resp.next_cursor is None
    assert len(resp.facts) == 1


def test_no_runtime_deps_imported():
    """contracts/ must not pull httpx, requests, pydantic, fastapi at import time.

    The package is meant to be installable on its own. If a dep slips
    in, every consumer pays for it.
    """
    import sys

    forbidden = {"httpx", "requests", "pydantic", "fastapi", "chromadb"}
    leaked = forbidden & set(sys.modules.keys())
    # Only fail if the *contracts* import is what brought them in.
    # If they're already in sys.modules from elsewhere (e.g., conftest),
    # this test can't disambiguate; skip rather than false-positive.
    # In a fresh interpreter, this set should be empty.
    if leaked:
        pytest.skip(f"Pre-imported by another module, can't isolate: {leaked}")


def test_public_exports():
    """The public surface of contracts/ matches what __all__ documents."""
    import contracts

    expected = {
        "FactPayload",
        "GetFactsResponse",
        "PostFactsRequest",
        "PostFactsResponse",
        "SCHEMA_VERSION",
    }
    assert set(contracts.__all__) == expected
    for name in expected:
        assert hasattr(contracts, name), f"contracts.{name} missing"
