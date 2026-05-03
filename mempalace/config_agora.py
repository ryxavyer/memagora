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
        "dry_run": true
      }
    }

Env vars override the file (pre-rename naming; these become
``MEMAGORA_*`` at v1.0):

* ``MEMPALACE_AGORA_ENDPOINT``
* ``MEMPALACE_AGORA_API_KEY``
* ``MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH``
* ``MEMPALACE_AGORA_DRY_RUN``  ("1"/"true"/"yes" → True; otherwise False)

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

    @property
    def enabled(self) -> bool:
        """True iff the wrapper should fire classifier hooks.

        ``endpoint=None`` is the explicit opt-out; the wrapper behaves
        as a transparent passthrough in that case.
        """
        return self.endpoint is not None


def _coerce_bool(raw: Optional[str], *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


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

    return AgoraConfig(
        endpoint=endpoint or None,
        api_key=api_key or None,
        classifier_prompt_path=classifier_prompt_path or None,
        dry_run=dry_run,
    )
