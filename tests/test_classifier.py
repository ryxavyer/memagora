"""Tests for mempalace.classifier — real LLM-driven behavior (v0.2)."""

import inspect
import json
from unittest.mock import MagicMock

import pytest

from contracts import FactPayload
from mempalace.classifier import (
    _coerce_fact,
    _extract_turn,
    _parse_response,
    _read_recent_turns,
    classify_text,
    classify_transcript,
)
from mempalace.config_agora import AgoraConfig


# ── Fixtures ────────────────────────────────────────────────────────────


def _llm_response_json(facts: list[dict]) -> str:
    """Build a canned LLM response text — JSON array."""
    return json.dumps(facts)


def _mock_provider_returning(text: str):
    """Build a MagicMock provider whose .classify(...) returns text."""
    provider = MagicMock()
    provider.classify.return_value = MagicMock(text=text)
    return provider


@pytest.fixture
def anthropic_cfg(monkeypatch):
    """An AgoraConfig with explicit api_key set so no env-var lookups happen."""
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://test.example/agora")
    return AgoraConfig(
        endpoint="https://test.example/agora",
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        llm_api_key="test-key",
        max_facts_per_turn=5,
        transcript_last_n=30,
    )


# ── _coerce_fact ────────────────────────────────────────────────────────


def test_coerce_fact_happy_path():
    item = {
        "subject": "project",
        "predicate": "uses",
        "object": "PostgreSQL",
        "confidence": 0.9,
    }
    fact = _coerce_fact(item, source_session_id="sess-1")
    assert fact is not None
    assert fact.subject == "project"
    assert fact.predicate == "uses"
    assert fact.object == "PostgreSQL"
    assert fact.confidence == 0.9
    assert fact.source_session_id == "sess-1"


def test_coerce_fact_strips_whitespace():
    item = {"subject": " Alice ", "predicate": " owns ", "object": " auth ", "confidence": 0.85}
    fact = _coerce_fact(item, source_session_id=None)
    assert fact.subject == "Alice"
    assert fact.predicate == "owns"
    assert fact.object == "auth"


def test_coerce_fact_below_threshold_dropped():
    """confidence < 0.6 → omitted entirely (engineer sovereignty)."""
    item = {"subject": "x", "predicate": "is", "object": "y", "confidence": 0.4}
    assert _coerce_fact(item, source_session_id=None) is None


def test_coerce_fact_clamps_confidence_to_unit_interval():
    """Pathological confidences clamp to [0, 1]."""
    item = {"subject": "x", "predicate": "is", "object": "y", "confidence": 2.5}
    fact = _coerce_fact(item, source_session_id=None)
    assert fact.confidence == 1.0


def test_coerce_fact_missing_field_returns_none():
    assert _coerce_fact({"subject": "x", "predicate": "is"}, source_session_id=None) is None


def test_coerce_fact_empty_strings_returns_none():
    item = {"subject": "", "predicate": "is", "object": "y", "confidence": 0.9}
    assert _coerce_fact(item, source_session_id=None) is None


def test_coerce_fact_non_string_field_returns_none():
    item = {"subject": 42, "predicate": "is", "object": "y", "confidence": 0.9}
    assert _coerce_fact(item, source_session_id=None) is None


def test_coerce_fact_non_dict_returns_none():
    assert _coerce_fact("not a dict", source_session_id=None) is None
    assert _coerce_fact(["not a dict"], source_session_id=None) is None


def test_coerce_fact_defaults_confidence_to_one_when_absent():
    item = {"subject": "x", "predicate": "is", "object": "y"}
    fact = _coerce_fact(item, source_session_id=None)
    assert fact is not None
    assert fact.confidence == 1.0


# ── _parse_response ─────────────────────────────────────────────────────


def test_parse_response_empty_input_returns_empty_list():
    assert _parse_response("") == []
    assert _parse_response("   ") == []


def test_parse_response_array_of_facts():
    raw = _llm_response_json(
        [
            {"subject": "a", "predicate": "is", "object": "b", "confidence": 0.9},
            {"subject": "c", "predicate": "is", "object": "d", "confidence": 0.8},
        ]
    )
    facts = _parse_response(raw, source_session_id="sess-1")
    assert len(facts) == 2
    assert facts[0].subject == "a"
    assert facts[1].subject == "c"
    assert all(f.source_session_id == "sess-1" for f in facts)


def test_parse_response_strips_code_fences():
    raw = (
        "```json\n"
        + _llm_response_json(
            [{"subject": "a", "predicate": "is", "object": "b", "confidence": 0.9}]
        )
        + "\n```"
    )
    facts = _parse_response(raw, source_session_id=None)
    assert len(facts) == 1


def test_parse_response_malformed_json_returns_empty():
    """An LLM returning prose instead of JSON must NOT crash — return []."""
    assert _parse_response("Hmm, I don't see any facts in that.", source_session_id=None) == []


def test_parse_response_non_array_returns_empty():
    """A JSON object (not array) at the top level must be rejected."""
    raw = json.dumps({"subject": "x", "predicate": "is", "object": "y"})
    assert _parse_response(raw, source_session_id=None) == []


def test_parse_response_filters_low_confidence_silently():
    raw = _llm_response_json(
        [
            {"subject": "a", "predicate": "is", "object": "b", "confidence": 0.9},
            {"subject": "c", "predicate": "is", "object": "d", "confidence": 0.4},
            {"subject": "e", "predicate": "is", "object": "f", "confidence": 0.8},
        ]
    )
    facts = _parse_response(raw, source_session_id=None)
    assert len(facts) == 2  # one filtered out


# ── classify_text ───────────────────────────────────────────────────────


def test_classify_text_empty_returns_empty(anthropic_cfg):
    assert classify_text("", config=anthropic_cfg) == []
    assert classify_text("   ", config=anthropic_cfg) == []


def test_classify_text_happy_path(anthropic_cfg, monkeypatch):
    """Mocked LLM → parsed FactPayload list with provenance stamped."""
    canned = _llm_response_json(
        [{"subject": "project", "predicate": "uses", "object": "PostgreSQL", "confidence": 0.9}]
    )
    mock_provider = _mock_provider_returning(canned)
    monkeypatch.setattr("mempalace.classifier.get_provider", lambda **kw: mock_provider)

    facts = classify_text("We're using PostgreSQL.", config=anthropic_cfg, source_session_id="s-42")
    assert len(facts) == 1
    assert facts[0].subject == "project"
    assert facts[0].source_session_id == "s-42"

    # Verify the provider was called with the system prompt + user text
    mock_provider.classify.assert_called_once()
    call_kwargs = mock_provider.classify.call_args.kwargs
    assert "extract structured facts" in call_kwargs["system"].lower()
    assert "PostgreSQL" in call_kwargs["user"]
    assert call_kwargs["json_mode"] is True


def test_classify_text_caps_at_max_facts_per_turn(monkeypatch):
    """If the LLM returns 10 facts but max is 3, only 3 come out."""
    cfg = AgoraConfig(
        endpoint="https://x", llm_provider="anthropic", llm_api_key="k", max_facts_per_turn=3
    )
    canned = _llm_response_json(
        [
            {"subject": str(i), "predicate": "is", "object": "y", "confidence": 0.9}
            for i in range(10)
        ]
    )
    monkeypatch.setattr(
        "mempalace.classifier.get_provider", lambda **kw: _mock_provider_returning(canned)
    )
    facts = classify_text("text", config=cfg)
    assert len(facts) == 3


def test_classify_text_returns_empty_on_llm_error(anthropic_cfg, monkeypatch):
    """LLMError is swallowed — engineer sovereignty, no facts leak on failure."""
    from mempalace.llm_client import LLMError

    mock_provider = MagicMock()
    mock_provider.classify.side_effect = LLMError("provider exploded")
    monkeypatch.setattr("mempalace.classifier.get_provider", lambda **kw: mock_provider)

    assert classify_text("text", config=anthropic_cfg) == []


def test_classify_text_returns_empty_on_malformed_llm_response(anthropic_cfg, monkeypatch):
    """LLM returns prose instead of JSON → empty list, no crash."""
    mock_provider = _mock_provider_returning("I'm not sure I can do that.")
    monkeypatch.setattr("mempalace.classifier.get_provider", lambda **kw: mock_provider)

    assert classify_text("text", config=anthropic_cfg) == []


def test_classify_text_requires_api_key_for_anthropic(monkeypatch):
    """No API key + anthropic provider → returns [] without calling LLM."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = AgoraConfig(endpoint="https://x", llm_provider="anthropic", llm_api_key=None)

    # Ensure get_provider is NOT called — the guard fires first.
    called = {"yes": False}

    def _trap(**kw):
        called["yes"] = True
        return _mock_provider_returning("[]")

    monkeypatch.setattr("mempalace.classifier.get_provider", _trap)
    assert classify_text("text", config=cfg) == []
    assert called["yes"] is False


def test_classify_text_uses_anthropic_api_key_env_fallback(monkeypatch):
    """When llm_api_key is unset but ANTHROPIC_API_KEY is, the call succeeds."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    cfg = AgoraConfig(endpoint="https://x", llm_provider="anthropic")  # no llm_api_key

    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return _mock_provider_returning("[]")

    monkeypatch.setattr("mempalace.classifier.get_provider", _capture)
    classify_text("text", config=cfg)

    assert captured["api_key"] == "from-env"


def test_classify_text_ollama_needs_no_key(monkeypatch):
    """Ollama provider must work with no API key configured anywhere."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = AgoraConfig(endpoint="https://x", llm_provider="ollama", llm_model="llama3.1")

    monkeypatch.setattr(
        "mempalace.classifier.get_provider", lambda **kw: _mock_provider_returning("[]")
    )
    # Returns empty (no facts in canned response) but did NOT short-circuit on the key guard
    assert classify_text("text", config=cfg) == []


# ── _read_recent_turns + classify_transcript ────────────────────────────


def _write_transcript(path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_extract_turn_user_text():
    entry = {"message": {"role": "user", "content": "hello"}}
    assert _extract_turn(entry) == ("user", "hello")


def test_extract_turn_assistant_block_content():
    """Claude Code may use content blocks for assistant turns."""
    entry = {
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi there"}, {"type": "tool_use", "id": "x"}],
        }
    }
    assert _extract_turn(entry) == ("assistant", "Hi there")


def test_extract_turn_skips_command_message():
    entry = {"message": {"role": "user", "content": "<command-message>foo"}}
    assert _extract_turn(entry) is None


def test_extract_turn_skips_system_reminder():
    entry = {"message": {"role": "user", "content": "<system-reminder>foo"}}
    assert _extract_turn(entry) is None


def test_extract_turn_skips_non_user_assistant_roles():
    entry = {"message": {"role": "system", "content": "ignore me"}}
    assert _extract_turn(entry) is None


def test_extract_turn_empty_content_returns_none():
    entry = {"message": {"role": "user", "content": ""}}
    assert _extract_turn(entry) is None


def test_read_recent_turns_missing_file_returns_empty(tmp_path):
    assert _read_recent_turns(tmp_path / "nope.jsonl", last_n=10) == []


def test_read_recent_turns_keeps_last_n_user_turns_with_assistant_replies(tmp_path):
    """Last N user turns and any assistant turns that come after them are kept."""
    entries = [
        {"message": {"role": "user", "content": "u1"}},
        {"message": {"role": "assistant", "content": "a1"}},
        {"message": {"role": "user", "content": "u2"}},
        {"message": {"role": "assistant", "content": "a2"}},
        {"message": {"role": "user", "content": "u3"}},
        {"message": {"role": "assistant", "content": "a3"}},
    ]
    path = tmp_path / "transcript.jsonl"
    _write_transcript(path, entries)
    turns = _read_recent_turns(path, last_n=2)
    # last 2 user turns are u2 and u3 → window starts at u2
    assert turns == [("user", "u2"), ("assistant", "a2"), ("user", "u3"), ("assistant", "a3")]


def test_classify_transcript_happy_path(anthropic_cfg, tmp_path, monkeypatch):
    entries = [
        {"message": {"role": "user", "content": "Decided to use PostgreSQL."}},
        {"message": {"role": "assistant", "content": "Got it."}},
    ]
    path = tmp_path / "transcript.jsonl"
    _write_transcript(path, entries)

    canned = _llm_response_json(
        [{"subject": "project", "predicate": "uses", "object": "PostgreSQL", "confidence": 0.9}]
    )
    mock_provider = _mock_provider_returning(canned)
    monkeypatch.setattr("mempalace.classifier.get_provider", lambda **kw: mock_provider)

    facts = classify_transcript(path, config=anthropic_cfg, source_session_id="s-1")
    assert len(facts) == 1
    assert facts[0].subject == "project"

    # Verify the user-facing text included both roles
    call_user = mock_provider.classify.call_args.kwargs["user"]
    assert "Decided to use PostgreSQL." in call_user
    assert "Got it." in call_user


def test_classify_transcript_empty_file_returns_empty(anthropic_cfg, tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text("", encoding="utf-8")
    assert classify_transcript(path, config=anthropic_cfg) == []


def test_classify_transcript_uses_config_default_for_last_n(anthropic_cfg, tmp_path, monkeypatch):
    """If last_n is None, fall back to config.transcript_last_n."""
    entries = [{"message": {"role": "user", "content": f"msg {i}"}} for i in range(50)]
    path = tmp_path / "transcript.jsonl"
    _write_transcript(path, entries)

    captured = {}
    monkeypatch.setattr(
        "mempalace.classifier.get_provider",
        lambda **kw: _mock_provider_returning("[]"),
    )
    monkeypatch.setattr(
        "mempalace.classifier._read_recent_turns",
        lambda path, *, last_n: (captured.__setitem__("last_n", last_n), [])[1],
    )
    classify_transcript(path, config=anthropic_cfg)
    assert captured["last_n"] == 30  # AgoraConfig default


# ── Signature invariants ────────────────────────────────────────────────


def test_classify_text_signature_kwargs_only():
    sig = inspect.signature(classify_text)
    for name in ("config", "source_session_id"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_classify_transcript_signature_kwargs_only():
    sig = inspect.signature(classify_transcript)
    for name in ("last_n", "config", "source_session_id"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_classify_text_returns_list_of_factpayload(anthropic_cfg, monkeypatch):
    monkeypatch.setattr(
        "mempalace.classifier.get_provider",
        lambda **kw: _mock_provider_returning(
            _llm_response_json(
                [{"subject": "x", "predicate": "is", "object": "y", "confidence": 0.9}]
            )
        ),
    )
    result = classify_text("text", config=anthropic_cfg)
    assert isinstance(result, list)
    assert all(isinstance(f, FactPayload) for f in result)
