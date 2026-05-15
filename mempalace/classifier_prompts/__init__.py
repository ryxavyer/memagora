"""Classifier prompts shipped with MemAgora.

The default prompt lives at ``default.md`` next to this file. Engineers
can override per-deployment via ``AgoraConfig.classifier_prompt_path``
(or ``MEMPALACE_AGORA_CLASSIFIER_PROMPT_PATH`` env var).
"""

from pathlib import Path

DEFAULT_PROMPT_PATH = Path(__file__).parent / "default.md"


def load_default_prompt() -> str:
    """Return the default classifier prompt text bundled with MemAgora."""
    return DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
