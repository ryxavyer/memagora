"""MemAgora classifier — decides which structured facts cross from a
local palace to the team agora.

v0.1 stub: returns ``[]`` for any input. Real classifier behavior — a
prompt + LLM call that emits ``FactPayload`` objects with confidence
scores — lands in v0.2.

Engineer sovereignty principle (see AGENTS.md): when uncertain, the
fact stays in the palace. The default classifier MUST be conservative.
"""

from typing import Optional

from contracts import FactPayload


def classify(
    text: str,
    *,
    prompt_path: Optional[str] = None,
    source_session_id: Optional[str] = None,
) -> list[FactPayload]:
    """Classify ``text`` into structured facts (v0.1 stub).

    Args:
        text: Raw conversation chunk to extract facts from.
        prompt_path: Path to a deployment-tuned classifier prompt.
                     Currently ignored — v0.2 wires it in.
        source_session_id: Engineer's local session ID, for provenance.

    Returns:
        List of ``FactPayload``. Empty in v0.1.
    """
    # v0.2 will replace this stub with an LLM call against `prompt_path`,
    # parse the response into FactPayload objects, and return them. Until
    # then the classifier emits nothing — the backend wrapper still
    # writes audit entries and the dry-run path still exercises the
    # full plumbing.
    return []
