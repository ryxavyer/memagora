"""MemAgora-specific configuration: classifier + agora endpoint settings.

Lives separate from the inherited ``mempalace/config.py`` so the
inherited config layer stays untouched while MemAgora is in skeleton
phase. Mirrors the same load-order pattern (env vars > config file >
defaults) so engineers familiar with MempalaceConfig don't have to
learn a new mental model.

Reads the ``agora`` section of ``~/.mempalace/config.json``::

    {
      "agora": {
        "endpoint": "https://team.example.com/agora",
        "api_key": "...",
        "classifier_prompt_path": "/path/to/prompt.txt",
        "dry_run": true,
        "llm_provider": "anthropic",
        "llm_model": "claude-haiku-4-5-20251001",
        "llm_endpoint": null,
        "llm_api_key": null,
        "max_facts_per_turn": 5,
        "transcript_last_n": 30
      }
    }

Env vars override the file (pre-rename naming; these become
``MEMAGORA_*`` at v1.0):

* ``MEMPALACE_AGORA_ENDPOINT`` / ``_API_KEY`` / ``_CLASSIFIER_PROMPT_PATH`` /
  ``_DRY_RUN`` — server endpoint + dry-run flag
* ``MEMPALACE_AGORA_LLM_PROVIDER`` / ``_LLM_MODEL`` / ``_LLM_ENDPOINT`` /
  ``_LLM_API_KEY`` — classifier LLM configuration
* ``MEMPALACE_AGORA_MAX_FACTS_PER_TURN`` / ``_TRANSCRIPT_LAST_N`` — safety caps

Default classifier LLM is the Anthropic provider with Claude Haiku 4.5 — the
zero-config path for Claude Code users (who already have ``ANTHROPIC_API_KEY``
in their environment). ``llm_api_key`` falls back to ``ANTHROPIC_API_KEY`` when
provider is ``anthropic`` and to ``OPENAI_API_KEY`` when provider is
``openai-compat``. Ollama requires no key.

Behavior:

* If ``endpoint`` is ``None``, MemAgora is opt-out — the backend wrapper
  becomes a pure passthrough and writes no audit entries.
* If ``endpoint`` is set, classifier hooks fire and audit entries are
  written. ``dry_run=True`` (the v0.1 default) prevents the actual
  network POST while still recording what would have been sent.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_DIR = Path.home() / ".mempalace"
DEFAULT_LLM_PROVIDER = "anthropic"
DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_FACTS_PER_TURN = 5
DEFAULT_TRANSCRIPT_LAST_N = 30
_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class AgoraConfig:
    """MemAgora classifier + endpoint settings, loaded from disk + env.

    Frozen on purpose — config is read once at backend construction and
    treated as immutable for the life of the wrapper. Reload by
    constructing a new ``AgoraBackend``.
    """

    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    classifier_prompt_path: Optional[str] = None
    dry_run: bool = True
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODEL
    llm_endpoint: Optional[str] = None
    llm_api_key: Optional[str] = None
    max_facts_per_turn: int = DEFAULT_MAX_FACTS_PER_TURN
    transcript_last_n: int = DEFAULT_TRANSCRIPT_LAST_N

    @property
    def enabled(self) -> bool:
        """True iff the wrapper should fire classifier hooks.

        ``endpoint=None`` is the explicit opt-out; the wrapper behaves
        as a transparent passthrough in that case.
        """
        return self.endpoint is not None

    def resolve_llm_api_key(self) -> Optional[str]:
        """Resolve the LLM API key, falling back to provider-specific env vars.

        Resolution order:
          1. ``self.llm_api_key`` (if set)
          2. Provider-specific env var:
             - ``anthropic`` → ``ANTHROPIC_API_KEY``
             - ``openai-compat`` → ``OPENAI_API_KEY``
             - ``ollama`` → no key needed; returns ``None``
        """
        if self.llm_api_key:
            return self.llm_api_key
        if self.llm_provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        if self.llm_provider == "openai-compat":
            return os.environ.get("OPENAI_API_KEY")
        return None


def _coerce_bool(raw: Optional[str], *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _coerce_int(raw: Optional[str], *, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def load_agora_config(*, config_dir: Optional[Path] = None) -> AgoraConfig:
    """Load AgoraConfig with env-var overrides on top of the JSON file."""
    cfg_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    cfg_file = cfg_dir / "config.json"

    file_section: dict = {}
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as fh:
                file_section = json.load(fh).get("agora", {}) or {}
        except (json.JSONDecodeError, OSError):
            file_section = {}

    endpoint = os.environ.get("MEMPALACE_AGORA_ENDPOINT") or file_section.get("endpoint")
    api_key = os.environ.get("MEMPALACE_AGORA_API_KEY") or file_section.get("api_key")
    classifier_prompt_path = os.environ.get(
        "MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH"
    ) or file_section.get("classifier_prompt_path")

    dry_run = _coerce_bool(
        os.environ.get("MEMPALACE_AGORA_DRY_RUN"),
        default=bool(file_section.get("dry_run", True)),
    )

    llm_provider = (
        os.environ.get("MEMPALACE_AGORA_LLM_PROVIDER")
        or file_section.get("llm_provider")
        or DEFAULT_LLM_PROVIDER
    )
    llm_model = (
        os.environ.get("MEMPALACE_AGORA_LLM_MODEL")
        or file_section.get("llm_model")
        or DEFAULT_LLM_MODEL
    )
    llm_endpoint = os.environ.get("MEMPALACE_AGORA_LLM_ENDPOINT") or file_section.get(
        "llm_endpoint"
    )
    llm_api_key = os.environ.get("MEMPALACE_AGORA_LLM_API_KEY") or file_section.get("llm_api_key")

    max_facts_per_turn = _coerce_int(
        os.environ.get("MEMPALACE_AGORA_MAX_FACTS_PER_TURN"),
        default=int(file_section.get("max_facts_per_turn", DEFAULT_MAX_FACTS_PER_TURN)),
    )
    transcript_last_n = _coerce_int(
        os.environ.get("MEMPALACE_AGORA_TRANSCRIPT_LAST_N"),
        default=int(file_section.get("transcript_last_n", DEFAULT_TRANSCRIPT_LAST_N)),
    )

    return AgoraConfig(
        endpoint=endpoint or None,
        api_key=api_key or None,
        classifier_prompt_path=classifier_prompt_path or None,
        dry_run=dry_run,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_endpoint=llm_endpoint or None,
        llm_api_key=llm_api_key or None,
        max_facts_per_turn=max_facts_per_turn,
        transcript_last_n=transcript_last_n,
    )
