"""MemAgora classifier — decides which structured facts cross from a
local palace to the team agora.

v0.2 wires this to a real LLM via the inherited ``llm_client``. Behavior:

1. Read the last N turns of a Claude Code JSONL transcript (or a raw
   conversation text blob).
2. Send to an LLM with a conservative system prompt — default ships at
   ``classifier_prompts/default.md``; deployments can override via
   ``AgoraConfig.classifier_prompt_path``.
3. Parse the JSON response into ``FactPayload`` objects.
4. Cap at ``AgoraConfig.max_facts_per_turn`` so a single classifier run
   can't flood the agora.

Engineer sovereignty (see AGENTS.md): every error path returns an empty
list. A misbehaving LLM, a parse failure, a missing API key — none of
them result in raw conversation content leaking to the team graph.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from contracts import FactPayload
from .classifier_prompts import load_default_prompt
from .config_agora import AgoraConfig, load_agora_config
from .llm_client import LLMError, get_provider


logger = logging.getLogger(__name__)

# Strip markdown code fences from LLM responses — some providers wrap
# JSON in ```json ... ``` blocks even when asked for raw JSON.
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# ── Public API ──────────────────────────────────────────────────────────


def classify_text(
    text: str,
    *,
    config: Optional[AgoraConfig] = None,
    source_session_id: Optional[str] = None,
) -> list[FactPayload]:
    """Run the classifier on a raw conversation text blob.

    Returns a list of ``FactPayload`` objects, capped at
    ``config.max_facts_per_turn``. Returns ``[]`` on any error
    (LLM failure, malformed response, etc.) — engineer sovereignty.
    """
    if not text or not text.strip():
        return []

    cfg = config if config is not None else load_agora_config()

    try:
        system_prompt = _load_prompt(cfg.classifier_prompt_path)
    except OSError:
        logger.exception("Could not read classifier prompt; emitting no facts.")
        return []

    api_key = cfg.resolve_llm_api_key()
    if cfg.llm_provider in ("anthropic", "openai-compat") and not api_key:
        logger.warning(
            "Classifier LLM provider %r requires an API key but none is configured; "
            "set MEMPALACE_AGORA_LLM_API_KEY or the provider-specific env var.",
            cfg.llm_provider,
        )
        return []

    try:
        provider = get_provider(
            name=cfg.llm_provider,
            model=cfg.llm_model,
            endpoint=cfg.llm_endpoint,
            api_key=api_key,
        )
        response = provider.classify(system=system_prompt, user=text, json_mode=True)
    except LLMError:
        logger.exception("LLM call failed; emitting no facts.")
        return []

    facts = _parse_response(response.text, source_session_id=source_session_id)
    if len(facts) > cfg.max_facts_per_turn:
        logger.info(
            "Classifier returned %d facts; capping at max_facts_per_turn=%d.",
            len(facts),
            cfg.max_facts_per_turn,
        )
        facts = facts[: cfg.max_facts_per_turn]
    return facts


def classify_transcript(
    transcript_path: Path,
    *,
    last_n: Optional[int] = None,
    config: Optional[AgoraConfig] = None,
    source_session_id: Optional[str] = None,
) -> list[FactPayload]:
    """Classify the last ``last_n`` turns of a Claude Code JSONL transcript.

    If ``last_n`` is ``None``, uses ``config.transcript_last_n``.

    Returns ``[]`` if the transcript can't be read or is empty.
    """
    cfg = config if config is not None else load_agora_config()
    n = last_n if last_n is not None else cfg.transcript_last_n

    turns = _read_recent_turns(transcript_path, last_n=n)
    if not turns:
        return []

    text = "\n\n".join(f"{role}: {content}" for role, content in turns)
    return classify_text(text, config=cfg, source_session_id=source_session_id)


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_prompt(custom_path: Optional[str]) -> str:
    """Load the classifier system prompt. Custom path wins over default."""
    if custom_path:
        return Path(custom_path).expanduser().read_text(encoding="utf-8")
    return load_default_prompt()


def _parse_response(
    raw: str,
    *,
    source_session_id: Optional[str] = None,
) -> list[FactPayload]:
    """Parse an LLM response into a list of FactPayload.

    Tolerates code fences and surrounding whitespace. Returns ``[]`` on
    any parse failure — engineer sovereignty: no half-baked facts leak.
    """
    if not raw or not raw.strip():
        return []

    cleaned = _CODE_FENCE_RE.sub("", raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Classifier response was not valid JSON; emitting no facts.")
        return []

    if not isinstance(data, list):
        logger.warning("Classifier response was not a JSON array; emitting no facts.")
        return []

    facts: list[FactPayload] = []
    for item in data:
        fact = _coerce_fact(item, source_session_id=source_session_id)
        if fact is not None:
            facts.append(fact)
    return facts


def _coerce_fact(
    item: object,
    *,
    source_session_id: Optional[str],
) -> Optional[FactPayload]:
    """Coerce a single LLM array element into a FactPayload.

    Returns ``None`` if the item is missing required fields or has the
    wrong shape. Confidence below 0.6 is dropped per the default-prompt
    contract (low-confidence facts must be omitted, not labeled).
    """
    if not isinstance(item, dict):
        return None
    subject = item.get("subject")
    predicate = item.get("predicate")
    obj = item.get("object")
    if not (isinstance(subject, str) and isinstance(predicate, str) and isinstance(obj, str)):
        return None
    if not (subject.strip() and predicate.strip() and obj.strip()):
        return None

    confidence_raw = item.get("confidence", 1.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.6:
        return None

    return FactPayload(
        subject=subject.strip(),
        predicate=predicate.strip(),
        object=obj.strip(),
        confidence=confidence,
        source_session_id=source_session_id,
    )


def _read_recent_turns(
    transcript_path: Path,
    *,
    last_n: int,
) -> list[tuple[str, str]]:
    """Read the last ``last_n`` user turns plus their assistant responses
    from a Claude Code JSONL transcript.

    Returns a list of ``(role, content)`` tuples in chronological order.
    Filters out command-messages, system-reminders, and tool-call chrome
    so the classifier sees clean conversation content.

    Returns ``[]`` if the transcript is missing or empty.
    """
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return []

    all_turns: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turn = _extract_turn(entry)
                if turn is not None:
                    all_turns.append(turn)
    except OSError:
        return []

    # Last N user turns; include any assistant turns that come after each.
    user_indices = [i for i, (role, _) in enumerate(all_turns) if role == "user"]
    if not user_indices:
        return []
    start = user_indices[-last_n] if len(user_indices) >= last_n else user_indices[0]
    return all_turns[start:]


def _extract_turn(entry: dict) -> Optional[tuple[str, str]]:
    """Extract a single (role, content) turn from a JSONL entry.

    Returns ``None`` if the entry is hook chrome, a system reminder, a
    command message, or any other non-conversational artifact.
    """
    msg = entry.get("message") or entry.get("event_message")
    if not isinstance(msg, dict):
        return None
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None

    content = msg.get("content", "")
    if isinstance(content, list):
        # Claude Code uses content blocks: {type: text|tool_use|tool_result, ...}
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        content = "\n".join(parts)

    if not isinstance(content, str) or not content.strip():
        return None
    # Filter hook chrome.
    if "<command-message>" in content or "<system-reminder>" in content:
        return None
    return role, content.strip()
