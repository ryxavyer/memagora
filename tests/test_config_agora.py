"""Tests for mempalace.config_agora — AgoraConfig loading."""

import json

import pytest

from mempalace.config_agora import AgoraConfig, load_agora_config


# ── Default behavior ────────────────────────────────────────────────────


def test_defaults_when_no_file_no_env(tmp_path, monkeypatch):
    """No config file + no env vars → all None, dry_run defaults True, disabled."""
    for var in (
        "MEMPALACE_AGORA_ENDPOINT",
        "MEMPALACE_AGORA_API_KEY",
        "MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH",
        "MEMPALACE_AGORA_DRY_RUN",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.endpoint is None
    assert cfg.api_key is None
    assert cfg.classifier_prompt_path is None
    assert cfg.dry_run is True
    assert cfg.enabled is False


def test_enabled_when_endpoint_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://example.com/agora")
    cfg = load_agora_config(config_dir=tmp_path)
    assert cfg.enabled is True
    assert cfg.endpoint == "https://example.com/agora"


# ── Config file ─────────────────────────────────────────────────────────


def test_loads_from_config_json(tmp_path, monkeypatch):
    """All four fields read from the agora section of config.json."""
    for var in (
        "MEMPALACE_AGORA_ENDPOINT",
        "MEMPALACE_AGORA_API_KEY",
        "MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH",
        "MEMPALACE_AGORA_DRY_RUN",
    ):
        monkeypatch.delenv(var, raising=False)

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agora": {
                    "endpoint": "https://team.example/agora",
                    "api_key": "secret-key",
                    "classifier_prompt_path": "/path/to/prompt.txt",
                    "dry_run": False,
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


# ── Frozen dataclass invariant ──────────────────────────────────────────


def test_agora_config_is_frozen():
    """AgoraConfig is treated as immutable post-load."""
    from dataclasses import FrozenInstanceError

    cfg = AgoraConfig(endpoint="https://x")
    with pytest.raises(FrozenInstanceError):
        cfg.endpoint = "https://y"
