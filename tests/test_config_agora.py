"""Tests for mempalace.config_agora — AgoraConfig loading."""

import json

import pytest

from mempalace.config_agora import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MAX_FACTS_PER_TURN,
    DEFAULT_TRANSCRIPT_LAST_N,
    AgoraConfig,
    load_agora_config,
)


_ALL_AGORA_ENV_VARS = (
    "MEMPALACE_AGORA_ENDPOINT",
    "MEMPALACE_AGORA_API_KEY",
    "MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH",
    "MEMPALACE_AGORA_DRY_RUN",
    "MEMPALACE_AGORA_LLM_PROVIDER",
    "MEMPALACE_AGORA_LLM_MODEL",
    "MEMPALACE_AGORA_LLM_ENDPOINT",
    "MEMPALACE_AGORA_LLM_API_KEY",
    "MEMPALACE_AGORA_MAX_FACTS_PER_TURN",
    "MEMPALACE_AGORA_TRANSCRIPT_LAST_N",
)


def _clear_env(monkeypatch):
    """Clear every MEMPALACE_AGORA_* env var (plus provider-fallback keys).

    Tests run inside a session-scoped HOME isolation (conftest), but
    environment variables can leak from the host. Clearing here keeps
    every test deterministic regardless of who launched the suite.
    """
    for var in _ALL_AGORA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ── Default behavior ────────────────────────────────────────────────────


def test_defaults_when_no_file_no_env(tmp_path, monkeypatch):
    """No config file + no env vars → all None, dry_run defaults True, disabled."""
    _clear_env(monkeypatch)

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint is None
    assert cfg.api_key is None
    assert cfg.classifier_prompt_path is None
    assert cfg.dry_run is True
    assert cfg.enabled is False
    # LLM defaults match the Claude Code zero-config path
    assert cfg.llm_provider == DEFAULT_LLM_PROVIDER == "anthropic"
    assert cfg.llm_model == DEFAULT_LLM_MODEL
    assert cfg.llm_endpoint is None
    assert cfg.llm_api_key is None
    assert cfg.max_facts_per_turn == DEFAULT_MAX_FACTS_PER_TURN == 5
    assert cfg.transcript_last_n == DEFAULT_TRANSCRIPT_LAST_N == 30


def test_enabled_when_endpoint_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://example.com/agora")
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.enabled is True
    assert cfg.endpoint == "https://example.com/agora"


# ── Config file ─────────────────────────────────────────────────────────


def test_loads_from_config_json(tmp_path, monkeypatch):
    """All fields read from the agora section of config.json."""
    _clear_env(monkeypatch)

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agora": {
                    "endpoint": "https://team.example/agora",
                    "api_key": "secret-key",
                    "classifier_prompt_path": "/path/to/prompt.txt",
                    "dry_run": False,
                    "llm_provider": "openai-compat",
                    "llm_model": "gpt-4o-mini",
                    "llm_endpoint": "https://api.openai.com/v1",
                    "llm_api_key": "sk-test",
                    "max_facts_per_turn": 10,
                    "transcript_last_n": 50,
                }
            }
        )
    )

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint == "https://team.example/agora"
    assert cfg.api_key == "secret-key"
    assert cfg.classifier_prompt_path == "/path/to/prompt.txt"
    assert cfg.dry_run is False
    assert cfg.enabled is True
    assert cfg.llm_provider == "openai-compat"
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.llm_endpoint == "https://api.openai.com/v1"
    assert cfg.llm_api_key == "sk-test"
    assert cfg.max_facts_per_turn == 10
    assert cfg.transcript_last_n == 50


def test_missing_agora_section_is_ok(tmp_path):
    """A config.json with no ``agora`` key produces all-default config."""
    (tmp_path / "config.json").write_text(json.dumps({"palace_path": "/somewhere"}))

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint is None
    assert cfg.dry_run is True


def test_malformed_json_falls_back_to_defaults(tmp_path):
    """Bad JSON should not crash startup — degrade to defaults."""
    (tmp_path / "config.json").write_text("{ this is not valid json")

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint is None
    assert cfg.dry_run is True


# ── Env-var precedence ──────────────────────────────────────────────────


def test_env_overrides_file(tmp_path, monkeypatch):
    """Env vars beat config.json values when both are present."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agora": {
                    "endpoint": "https://from-file.example",
                    "api_key": "file-key",
                    "dry_run": True,
                }
            }
        )
    )
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://from-env.example")
    monkeypatch.setenv("MEMPALACE_AGORA_API_KEY", "env-key")
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", "false")

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint == "https://from-env.example"
    assert cfg.api_key == "env-key"
    assert cfg.dry_run is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        ("garbage", False),
    ],
)
def test_dry_run_truthy_coercion(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", raw)
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.dry_run is expected


def test_env_endpoint_with_no_file(tmp_path, monkeypatch):
    """Env var alone is sufficient to enable; no file needed."""
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://env-only.example")
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.enabled is True
    assert cfg.endpoint == "https://env-only.example"


# ── LLM env-var precedence ──────────────────────────────────────────────


def test_llm_env_vars_override_file(tmp_path, monkeypatch):
    """MEMPALACE_AGORA_LLM_* env vars override the config.json agora.llm_* values."""
    _clear_env(monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agora": {
                    "llm_provider": "ollama",
                    "llm_model": "llama3.1",
                    "llm_endpoint": "http://localhost:11434",
                }
            }
        )
    )
    monkeypatch.setenv("MEMPALACE_AGORA_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MEMPALACE_AGORA_LLM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("MEMPALACE_AGORA_LLM_ENDPOINT", "https://api.anthropic.com")
    monkeypatch.setenv("MEMPALACE_AGORA_LLM_API_KEY", "env-key")

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_model == "claude-sonnet-4-6"
    assert cfg.llm_endpoint == "https://api.anthropic.com"
    assert cfg.llm_api_key == "env-key"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3", 3),
        ("0", 0),
        ("99", 99),
        ("", DEFAULT_MAX_FACTS_PER_TURN),
        ("garbage", DEFAULT_MAX_FACTS_PER_TURN),
    ],
)
def test_max_facts_per_turn_int_coercion(tmp_path, monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMPALACE_AGORA_MAX_FACTS_PER_TURN", raw)
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.max_facts_per_turn == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10", 10),
        ("100", 100),
        ("", DEFAULT_TRANSCRIPT_LAST_N),
        ("not-a-number", DEFAULT_TRANSCRIPT_LAST_N),
    ],
)
def test_transcript_last_n_int_coercion(tmp_path, monkeypatch, raw, expected):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMPALACE_AGORA_TRANSCRIPT_LAST_N", raw)
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.transcript_last_n == expected


# ── API key fallback ────────────────────────────────────────────────────


def test_resolve_llm_api_key_prefers_explicit_setting():
    """An explicit llm_api_key wins over provider-specific env vars."""
    cfg = AgoraConfig(llm_provider="anthropic", llm_api_key="explicit-key")
    assert cfg.resolve_llm_api_key() == "explicit-key"


def test_resolve_llm_api_key_falls_back_to_anthropic_env(monkeypatch):
    """When llm_provider=anthropic and llm_api_key is unset, ANTHROPIC_API_KEY wins."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-claude-code")
    cfg = AgoraConfig(llm_provider="anthropic")
    assert cfg.resolve_llm_api_key() == "from-claude-code"


def test_resolve_llm_api_key_falls_back_to_openai_env(monkeypatch):
    """When llm_provider=openai-compat and llm_api_key is unset, OPENAI_API_KEY wins."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    cfg = AgoraConfig(llm_provider="openai-compat")
    assert cfg.resolve_llm_api_key() == "sk-from-env"


def test_resolve_llm_api_key_ollama_returns_none(monkeypatch):
    """Ollama needs no key — resolve_llm_api_key returns None even if env vars are set."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-used")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")
    cfg = AgoraConfig(llm_provider="ollama")
    assert cfg.resolve_llm_api_key() is None


def test_resolve_llm_api_key_no_env_no_explicit_returns_none(monkeypatch):
    """If no key is available anywhere, return None — caller decides what to do."""
    _clear_env(monkeypatch)
    cfg = AgoraConfig(llm_provider="anthropic")
    assert cfg.resolve_llm_api_key() is None


# ── Frozen dataclass invariant ──────────────────────────────────────────


def test_agora_config_is_frozen():
    """AgoraConfig is treated as immutable post-load."""
    from dataclasses import FrozenInstanceError

    cfg = AgoraConfig(endpoint="https://x")
    with pytest.raises(FrozenInstanceError):
        cfg.endpoint = "https://y"
