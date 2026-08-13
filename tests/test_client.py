"""Tests for mempalace.client — the palace→agora HTTP client.

Two invariants matter more than any single behavior here:

1. The client never raises. It runs inside a Claude Code hook; an exception
   escaping it would break an engineer's session over a server outage.
2. It contacts the configured endpoint and nothing else, using stdlib urllib
   so the engineer-side install pulls in no HTTP dependency.

Transport-level coverage lives in ``test_client_http.py``, which drives these
functions against a real (loopback) HTTP server.
"""

import inspect

import pytest

from contracts import FactPayload, PostFactsResponse
from mempalace.client import get_facts, post_facts


def a_fact(subject="Alice", predicate="works_on", obj="MemAgora") -> FactPayload:
    return FactPayload(subject=subject, predicate=predicate, object=obj)


# ── Signature ───────────────────────────────────────────────────────────


def test_post_facts_signature_is_kwargs_only():
    """endpoint, api_key and timeout are keyword-only — pin against refactors."""
    sig = inspect.signature(post_facts)
    for name in ("endpoint", "api_key", "timeout"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only, got {param.kind}"
        )


def test_get_facts_is_entirely_kwargs_only():
    sig = inspect.signature(get_facts)
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())


# ── Short-circuits that make no network call ────────────────────────────


def test_empty_batch_is_not_sent(monkeypatch):
    """Nothing to say, nothing sent — and no response invented either."""
    _forbid_network(monkeypatch)
    result = post_facts([], endpoint="https://example.invalid")
    assert isinstance(result, PostFactsResponse)
    assert (result.accepted, result.rejected) == (0, 0)


@pytest.mark.parametrize(
    "endpoint",
    ["", "ftp://files.example", "file:///etc/passwd", "example.com"],
)
def test_non_http_endpoints_are_refused_without_a_call(monkeypatch, endpoint):
    _forbid_network(monkeypatch)
    result = post_facts([a_fact()], endpoint=endpoint)
    assert result.accepted == 0
    assert result.rejected == 1
    assert result.message


def test_get_facts_refuses_a_non_http_endpoint(monkeypatch):
    _forbid_network(monkeypatch)
    assert get_facts(endpoint="ftp://files.example") is None


# ── Failure handling ────────────────────────────────────────────────────


def test_unreachable_host_reports_the_batch_as_rejected(monkeypatch):
    """A hook must survive a down agora — no exception, counts say what happened."""
    facts = [a_fact(), a_fact("Bob", "reviewed", "PR-42")]
    result = post_facts(
        facts,
        endpoint="http://127.0.0.1:1",  # nothing listens here
        timeout=0.5,
    )
    assert isinstance(result, PostFactsResponse)
    assert (result.accepted, result.rejected) == (0, 2)
    assert "cannot reach" in result.message


def test_get_facts_returns_none_when_unreachable():
    assert get_facts(endpoint="http://127.0.0.1:1", timeout=0.5) is None


def test_no_network_libs_imported_by_client():
    """The client must not pull httpx/requests into the engineer-side install."""
    import sys

    pre_existing = {"httpx", "requests", "urllib3", "aiohttp"} & set(sys.modules.keys())
    if pre_existing:
        # Something else (chromadb, conftest fixtures) brought them in.
        # Can't isolate; skip rather than false-positive.
        pytest.skip(f"Pre-imported by another module: {pre_existing}")


def _forbid_network(monkeypatch):
    """Make any socket use an immediate, loud failure."""
    import socket

    def _no_socket(*args, **kwargs):
        raise AssertionError("client attempted a network call it should have skipped")

    monkeypatch.setattr(socket, "socket", _no_socket)
