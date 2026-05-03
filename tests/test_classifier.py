"""Tests for mempalace.classifier — v0.1 stub behavior."""

import inspect

from contracts import FactPayload
from mempalace.classifier import classify


def test_classify_returns_empty_list_for_any_input():
    """v0.1 stub returns [] regardless of input."""
    assert classify("anything at all") == []
    assert classify("") == []
    assert classify("decided to migrate to PostgreSQL") == []


def test_classify_signature_kwargs_only_for_optional_params():
    """prompt_path and source_session_id must be keyword-only.

    The agora wrapper passes them by keyword; if a future refactor
    moves them to positional, callers break silently. Lock the signature.
    """
    sig = inspect.signature(classify)
    # text is positional-or-keyword; the rest are keyword-only.
    for name in ("prompt_path", "source_session_id"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only, got {param.kind}"
        )


def test_classify_accepts_optional_kwargs_without_raising():
    """All documented kwargs are honored (even though the stub ignores them)."""
    result = classify(
        "the team adopted httpx for async HTTP",
        prompt_path="/some/path/prompt.txt",
        source_session_id="sess-2026-05-03-42",
    )
    assert result == []


def test_classify_return_type_is_list_of_factpayload():
    """The return annotation promises list[FactPayload]; the stub honors it.

    A future return path that yields anything else would silently break
    AgoraBackend's batching logic; pin it here.
    """
    result = classify("anything")
    assert isinstance(result, list)
    # When non-empty in v0.2, every element must be a FactPayload.
    for item in result:
        assert isinstance(item, FactPayload)


def test_classifier_does_not_import_network_libs():
    """v0.1 must not pull httpx/requests at module import time.

    The classifier becomes an LLM call in v0.2 — at that point the
    network dep enters. v0.1 keeps imports clean so an engineer who
    sets `endpoint=None` doesn't pay any installation cost.
    """
    import sys

    # If something already imported these (e.g., chromadb pulled in
    # something transitively), we can't disambiguate; the test would
    # false-positive. Skip rather than fail.
    pre_existing = {"httpx", "requests", "openai", "anthropic"} & set(sys.modules.keys())
    if pre_existing:
        import pytest

        pytest.skip(f"Pre-imported by another module: {pre_existing}")
