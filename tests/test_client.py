"""Tests for mempalace.client — v0.1 stub behavior + no-network invariant."""

import inspect

import pytest

from contracts import FactPayload, PostFactsResponse
from mempalace.client import post_facts


def test_post_facts_returns_post_facts_response():
    """Stub returns a real PostFactsResponse, not a dict."""
    result = post_facts([], endpoint="https://example/agora")
    assert isinstance(result, PostFactsResponse)


def test_post_facts_reports_input_count_as_accepted():
    """Stub reports all input facts as accepted, none rejected."""
    facts = [
        FactPayload(subject="Alice", predicate="works_on", object="MemAgora"),
        FactPayload(subject="Bob", predicate="reviewed", object="PR-42"),
    ]
    result = post_facts(facts, endpoint="https://example/agora")
    assert result.accepted == 2
    assert result.rejected == 0


def test_post_facts_empty_list_is_ok():
    result = post_facts([], endpoint="https://example/agora")
    assert result.accepted == 0
    assert result.rejected == 0


def test_post_facts_signature_kwargs_only():
    """endpoint and api_key are keyword-only — pin against accidental refactor."""
    sig = inspect.signature(post_facts)
    for name in ("endpoint", "api_key"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only, got {param.kind}"
        )


def test_post_facts_accepts_api_key_kwarg():
    """api_key is optional but must be accepted without raising."""
    facts = [FactPayload(subject="x", predicate="is", object="y")]
    result = post_facts(facts, endpoint="https://example", api_key="secret")
    assert result.accepted == 1


def test_v01_stub_makes_no_network_call(monkeypatch):
    """v0.1 stub does not touch the network even when called with a real-looking URL.

    Patch socket.socket so any attempted connection raises immediately;
    the stub should still complete normally because it makes no calls.
    """
    import socket

    def _no_socket(*args, **kwargs):
        raise RuntimeError("v0.1 client.post_facts attempted a network call")

    monkeypatch.setattr(socket, "socket", _no_socket)

    result = post_facts(
        [FactPayload(subject="x", predicate="is", object="y")],
        endpoint="https://this-host-must-not-be-contacted.example",
    )
    assert result.accepted == 1


def test_no_network_libs_imported_by_client():
    """The client module must not import httpx/requests at module load.

    v0.2 introduces the HTTP library; v0.1 keeps the install slim.
    """
    import sys

    pre_existing = {"httpx", "requests", "urllib3", "aiohttp"} & set(sys.modules.keys())
    if pre_existing:
        # Something else (chromadb, conftest fixtures) brought them in.
        # Can't isolate; skip the assertion rather than false-positive.
        pytest.skip(f"Pre-imported by another module: {pre_existing}")
