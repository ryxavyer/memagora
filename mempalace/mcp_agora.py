"""MemAgora MCP tools — the agent's interface to the team agora.

This is the primary path MemAgora was built for. The agent calling these tools
is the intelligence: it decides, at the moment it decides, what the rest of the
team needs to know and says so explicitly. No second LLM pass re-reads the
transcript to guess, and no API key or model dependency lives inside MemAgora
(see AGENTS.md, "Architectural Decisions").

Three surfaces:

* **Emission** — ``memagora_record_fact`` and ``memagora_record_decision``.
  Both mirror everything to the local audit log *before* anything leaves the
  machine, and both respect ``dry_run``.
* **Query** — ``memagora_facts_about``, ``memagora_timeline``,
  ``memagora_decisions_about``, ``memagora_why``. Read-only, and the only
  reason the palace ever talks to the agora on the read side.
* **Session start** — :func:`team_context`, which ``mempalace wake-up`` appends
  so a session opens knowing what the team already decided.

``dry_run`` defaults to ``True``. An engineer who has configured an endpoint
but not yet turned off dry-run gets full local recording and no network call,
and every tool result says so in words — an agent must never tell a user it
shared something it did not.
"""

import logging
import uuid
from typing import Optional

from contracts import DecisionRecord, FactClose, FactPayload

from .audit import write_audit_entry
from .config_agora import load_agora_config

logger = logging.getLogger(__name__)

NOT_CONFIGURED = (
    "No agora is configured for this palace, so there is nowhere to record team "
    "knowledge. Set MEMPALACE_AGORA_ENDPOINT and MEMPALACE_AGORA_API_KEY "
    "(see docs/deployment.md). Nothing was recorded."
)

DRY_RUN_NOTE = (
    "recorded to the local audit log; NOT sent to the team agora (dry-run mode). "
    "Tell the user it was captured locally only. Turn off dry-run with "
    "MEMPALACE_AGORA_DRY_RUN=0 once they are happy with what the audit log shows."
)


def new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:16]}"


# ── Emission ────────────────────────────────────────────────────────────


def tool_record_fact(
    subject: str,
    predicate: str,
    object: str,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    confidence: float = 1.0,
    decision_id: Optional[str] = None,
    supersedes: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """Record one team-relevant fact in the agora.

    ``supersedes`` names the object this fact replaces. Passing it closes the
    old fact and records the new one in a single request, which is the only way
    to keep "we use SQS" from sitting in the agora, still open, next to "we use
    Kinesis". Without it the team ends up with two current answers and no way
    to tell which one is stale.
    """
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "sent": False, "error": NOT_CONFIGURED}

    fact = FactPayload(
        subject=str(subject).strip(),
        predicate=str(predicate).strip(),
        object=str(object).strip(),
        valid_from=valid_from,
        valid_to=valid_to,
        confidence=_clamp(confidence),
        source_session_id=session_id,
        decision_id=decision_id,
    )
    if not (fact.subject and fact.predicate and fact.object):
        return {
            "success": False,
            "sent": False,
            "error": "subject, predicate and object are all required and must be non-empty",
        }

    closes = []
    if supersedes:
        closes.append(
            FactClose(
                subject=fact.subject,
                predicate=fact.predicate,
                object=str(supersedes).strip(),
                # The replacement's start date is the old fact's end date, so
                # the timeline has no gap and no overlap.
                valid_to=valid_from,
            )
        )

    _audit_emission(op="record_fact", cfg=cfg, session_id=session_id, facts=[fact], closes=closes)

    if cfg.dry_run:
        return {
            "success": True,
            "sent": False,
            "fact": _render_fact(fact),
            "supersedes": supersedes,
            "message": DRY_RUN_NOTE,
        }

    response = _safe_ingest(cfg, facts=[fact], closes=closes)
    _audit_post(cfg=cfg, session_id=session_id, response=response, facts=1, decisions=0)

    if response.facts_accepted:
        result = {
            "success": True,
            "sent": True,
            "fact": _render_fact(fact),
            "message": f"recorded in the team agora at {cfg.endpoint}",
        }
        if supersedes:
            result["superseded"] = response.facts_closed > 0
            if not response.facts_closed:
                result["warning"] = (
                    f"Recorded, but no open fact matched {fact.subject!r} → "
                    f"{fact.predicate!r} → {supersedes!r}, so nothing was closed. "
                    "The agora may now hold two current answers."
                )
        return result
    return {
        "success": False,
        "sent": False,
        "fact": _render_fact(fact),
        "error": response.message or "the agora rejected this fact",
    }


def tool_record_decision(
    title: str,
    chosen: str,
    rationale: str,
    decision_id: Optional[str] = None,
    alternatives_rejected: Optional[list] = None,
    constraints: Optional[list] = None,
    open_questions: Optional[list] = None,
    decided_on: Optional[str] = None,
    facts: Optional[list] = None,
    session_id: Optional[str] = None,
):
    """Record a decision — and, in the same call, the facts it produced.

    ``facts`` is a list of ``{"subject", "predicate", "object", …}`` mappings.
    They are linked to the decision automatically, so a later session asking
    "why is it this way" gets the argument and not just the conclusion.
    """
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "sent": False, "error": NOT_CONFIGURED}

    for name, value in (("title", title), ("chosen", chosen), ("rationale", rationale)):
        if not value or not str(value).strip():
            return {"success": False, "sent": False, "error": f"{name} is required"}

    decision = DecisionRecord(
        decision_id=decision_id or new_decision_id(),
        title=str(title).strip(),
        chosen=str(chosen).strip(),
        rationale=str(rationale).strip(),
        alternatives_rejected=_string_list(alternatives_rejected),
        constraints=_string_list(constraints),
        open_questions=_string_list(open_questions),
        decided_on=decided_on,
        source_session_id=session_id,
    )

    payloads, closes, error = _facts_from_mappings(facts, decision.decision_id, session_id)
    if error:
        return {"success": False, "sent": False, "error": error}

    _audit_emission(
        op="record_decision",
        cfg=cfg,
        session_id=session_id,
        facts=payloads,
        decision=decision,
        closes=closes,
    )

    if cfg.dry_run:
        return {
            "success": True,
            "sent": False,
            "decision_id": decision.decision_id,
            "facts_recorded": len(payloads),
            "message": DRY_RUN_NOTE,
        }

    response = _safe_ingest(cfg, facts=payloads, decisions=[decision], closes=closes)
    _audit_post(
        cfg=cfg,
        session_id=session_id,
        response=response,
        facts=len(payloads),
        decisions=1,
    )

    if response.decisions_accepted:
        return {
            "success": True,
            "sent": True,
            "decision_id": decision.decision_id,
            "facts_recorded": response.facts_accepted,
            "facts_superseded": response.facts_closed,
            "message": f"recorded in the team agora at {cfg.endpoint}",
        }
    return {
        "success": False,
        "sent": False,
        "decision_id": decision.decision_id,
        "error": response.message or "the agora rejected this decision",
    }


# ── Query ───────────────────────────────────────────────────────────────


def tool_facts_about(
    subject: str,
    as_of: Optional[str] = None,
    current: bool = True,
    limit: int = 25,
):
    """What the team knows about one entity."""
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "error": NOT_CONFIGURED}

    from .client import get_facts

    page = get_facts(
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        subject=subject,
        as_of=as_of,
        current=current and not as_of,
        limit=limit,
        timeout=cfg.post_timeout,
    )
    if page is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}

    facts = [_render_fact(f) for f in page.facts]
    result = {"success": True, "subject": subject, "count": len(facts), "facts": facts}
    conflicts = _conflicts(page.facts)
    if conflicts:
        result["conflicts"] = conflicts
        result["warning"] = (
            "Two or more open facts share a subject and predicate. The team may "
            "have superseded one without closing it — say so rather than picking one."
        )
    return result


def tool_timeline(subject: Optional[str] = None, limit: int = 25):
    """How the team's knowledge changed over time, oldest first."""
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "error": NOT_CONFIGURED}

    from .client import get_timeline

    page = get_timeline(
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        subject=subject,
        limit=limit,
        timeout=cfg.post_timeout,
    )
    if page is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}

    return {
        "success": True,
        "subject": subject,
        "count": len(page.facts),
        "timeline": [
            {
                "valid_from": fact.valid_from,
                "valid_to": fact.valid_to,
                "current": fact.valid_to is None,
                "fact": _render_fact(fact),
            }
            for fact in page.facts
        ],
    }


def tool_decisions_about(subject: str, limit: int = 10):
    """The decisions behind what the team knows about an entity."""
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "error": NOT_CONFIGURED}

    from .client import get_decisions, get_facts

    page = get_facts(
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        subject=subject,
        limit=max(limit * 4, 20),
        timeout=cfg.post_timeout,
    )
    if page is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}

    ids = _decision_ids(page.facts)
    if not ids:
        return {
            "success": True,
            "subject": subject,
            "count": 0,
            "decisions": [],
            "message": (
                f"The agora holds facts about {subject!r} but none of them are linked to a "
                "recorded decision — nobody captured the reasoning."
                if page.facts
                else f"The agora holds nothing about {subject!r}."
            ),
        }

    decisions = get_decisions(
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        ids=ids[:limit],
        timeout=cfg.post_timeout,
    )
    if decisions is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}

    return {
        "success": True,
        "subject": subject,
        "count": len(decisions.decisions),
        "decisions": [_render_decision(d) for d in decisions.decisions],
    }


def tool_why(subject: str, predicate: str):
    """Why does this hold? Returns the reasoning behind one relationship."""
    cfg = load_agora_config()
    if not cfg.enabled:
        return {"success": False, "error": NOT_CONFIGURED}

    from .client import get_decisions, get_facts

    page = get_facts(
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        subject=subject,
        predicate=predicate,
        current=True,
        limit=25,
        timeout=cfg.post_timeout,
    )
    if page is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}

    if not page.facts:
        return {
            "success": True,
            "found": False,
            "message": f"The agora holds no current fact for {subject!r} → {predicate!r}.",
        }

    result = {
        "success": True,
        "found": True,
        "facts": [_render_fact(f) for f in page.facts],
    }
    if len(page.facts) > 1:
        result["warning"] = (
            f"{len(page.facts)} facts are open for {subject!r} → {predicate!r} at once. "
            "They may contradict each other; report all of them rather than choosing."
        )

    ids = _decision_ids(page.facts)
    if not ids:
        result["decisions"] = []
        result["message"] = (
            "No decision is linked to this fact — it was recorded without its reasoning."
        )
        return result

    decisions = get_decisions(
        endpoint=cfg.endpoint, api_key=cfg.api_key, ids=ids, timeout=cfg.post_timeout
    )
    if decisions is None:
        return {"success": False, "error": f"could not reach the agora at {cfg.endpoint}"}
    result["decisions"] = [_render_decision(d) for d in decisions.decisions]
    return result


# ── Helpers ─────────────────────────────────────────────────────────────


def _safe_ingest(cfg, *, facts=None, decisions=None, closes=None):
    """Call the client, converting any unexpected failure into a response.

    ``post_ingest`` promises never to raise; this is the belt to that braces,
    because a raising MCP tool surfaces as an error inside the agent's session.
    """
    from contracts import IngestResponse

    from .client import post_ingest

    facts = list(facts or [])
    decisions = list(decisions or [])
    closes = list(closes or [])
    try:
        return post_ingest(
            endpoint=cfg.endpoint,
            facts=facts,
            decisions=decisions,
            closes=closes,
            api_key=cfg.api_key,
            timeout=cfg.post_timeout,
        )
    except Exception as exc:
        logger.exception("agora ingest raised")
        return IngestResponse(
            facts_accepted=0,
            facts_rejected=len(facts),
            decisions_accepted=0,
            decisions_rejected=len(decisions),
            message=f"client error: {exc}",
        )


def _audit_emission(*, op, cfg, session_id, facts, decision=None, closes=None):
    """Mirror every emission locally before anything crosses the boundary.

    One entry per fact and one per decision, so ``mempalace audit tail`` shows
    the engineer exactly what their agent chose to share.
    """
    import dataclasses

    if decision is not None:
        write_audit_entry(
            {
                "entry_type": "emit",
                "op": op,
                "session_id": session_id,
                "decision": dataclasses.asdict(decision),
                "dry_run": cfg.dry_run,
            }
        )
    for close in closes or []:
        write_audit_entry(
            {
                "entry_type": "emit",
                "op": "close_fact",
                "session_id": session_id,
                "close": dataclasses.asdict(close),
                "dry_run": cfg.dry_run,
            }
        )
    for fact in facts:
        write_audit_entry(
            {
                "entry_type": "emit",
                "op": op,
                "session_id": session_id,
                "fact": dataclasses.asdict(fact),
                "dry_run": cfg.dry_run,
            }
        )


def _audit_post(*, cfg, session_id, response, facts, decisions):
    write_audit_entry(
        {
            "entry_type": "post",
            "op": "ingested",
            "session_id": session_id,
            "endpoint": cfg.endpoint,
            "fact_count": facts,
            "decision_count": decisions,
            "accepted": response.facts_accepted + response.decisions_accepted,
            "rejected": response.facts_rejected + response.decisions_rejected,
            "message": response.message,
            "ok": (response.facts_accepted + response.decisions_accepted) > 0,
        }
    )


def _facts_from_mappings(raw_facts, decision_id, session_id):
    """Build ``FactPayload``s (and any closes) from the tool's ``facts`` argument."""
    payloads = []
    closes = []
    for item in raw_facts or []:
        if not isinstance(item, dict):
            return [], [], "each entry in facts must be an object with subject/predicate/object"
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        if not (subject and predicate and obj):
            return [], [], "each fact needs a non-empty subject, predicate and object"
        if item.get("supersedes"):
            closes.append(
                FactClose(
                    subject=subject,
                    predicate=predicate,
                    object=str(item["supersedes"]).strip(),
                    valid_to=item.get("valid_from"),
                )
            )
        payloads.append(
            FactPayload(
                subject=subject,
                predicate=predicate,
                object=obj,
                valid_from=item.get("valid_from"),
                valid_to=item.get("valid_to"),
                confidence=_clamp(item.get("confidence", 1.0)),
                source_session_id=session_id,
                decision_id=decision_id,
            )
        )
    return payloads, closes, None


def _decision_ids(facts) -> list:
    """Distinct decision ids across facts, in first-seen order."""
    seen = []
    for fact in facts:
        if fact.decision_id and fact.decision_id not in seen:
            seen.append(fact.decision_id)
    return seen


def _conflicts(facts) -> list:
    """Open facts that share a subject and predicate but disagree on the object.

    The agora cannot tell whether that is co-ownership or a superseded decision
    nobody closed, so it reports rather than resolves.
    """
    groups: dict = {}
    for fact in facts:
        if fact.valid_to is None:
            groups.setdefault((fact.subject, fact.predicate), []).append(fact.object)
    return [
        {"subject": subject, "predicate": predicate, "objects": objects}
        for (subject, predicate), objects in groups.items()
        if len(objects) > 1
    ]


def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _string_list(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _render_fact(fact) -> str:
    line = f"{fact.subject} --{fact.predicate}--> {fact.object}"
    if fact.valid_from or fact.valid_to:
        line += f" [{fact.valid_from or '…'} → {fact.valid_to or 'now'}]"
    return line


def _render_decision(decision) -> dict:
    return {
        "decision_id": decision.decision_id,
        "title": decision.title,
        "chosen": decision.chosen,
        "rationale": decision.rationale,
        "alternatives_rejected": list(decision.alternatives_rejected),
        "constraints": list(decision.constraints),
        "open_questions": list(decision.open_questions),
        "decided_on": decision.decided_on,
    }


# ── MCP registry ────────────────────────────────────────────────────────

_FACT_PROPERTIES = {
    "subject": {"type": "string", "description": "The entity the fact is about"},
    "predicate": {
        "type": "string",
        "description": "The relationship, snake_case (e.g. 'owned_by', 'uses', 'deprecated_on')",
    },
    "object": {"type": "string", "description": "The other side of the relationship"},
    "valid_from": {"type": "string", "description": "ISO date this became true (optional)"},
    "valid_to": {
        "type": "string",
        "description": "ISO date this stopped being true. Set it to close a superseded fact.",
    },
    "confidence": {"type": "number", "description": "0.0–1.0 (default 1.0)"},
    "supersedes": {
        "type": "string",
        "description": (
            "The object this fact replaces. Closes the old fact in the same request — use it "
            "whenever the team changed its mind, or the agora will hold both answers as current."
        ),
    },
}

TOOLS = {
    "memagora_record_fact": {
        "description": (
            "Record ONE fact the whole team needs, in the shared agora. Use for ownership, "
            "contracts, deprecations, and conventions — things a teammate's agent would be "
            "wrong not to know. Not for exploration, debugging, or anything specific to this "
            "engineer's machine. Prefer memagora_record_decision when a choice was made, so "
            "the reasoning travels with the fact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FACT_PROPERTIES,
                "decision_id": {
                    "type": "string",
                    "description": "Link to a decision recorded earlier (optional)",
                },
                "session_id": {"type": "string", "description": "This session's id (optional)"},
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_record_fact,
    },
    "memagora_record_decision": {
        "description": (
            "Record a decision and the facts it produced, at the moment it is made, while the "
            "alternatives are still in context. This is what answers 'why is it this way' for "
            "a teammate six months from now. Include what was rejected and why — a decision "
            "without its alternatives is just an assertion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "One line naming the decision"},
                "chosen": {"type": "string", "description": "The approach taken"},
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why it was taken. Write the argument in your own words — never paste "
                        "conversation transcript."
                    ),
                },
                "alternatives_rejected": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Options considered and why each lost",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What bounded the choice (deadlines, dependencies, conventions)",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What this decision deliberately left unresolved",
                },
                "decided_on": {"type": "string", "description": "ISO date (optional)"},
                "facts": {
                    "type": "array",
                    "description": "Atomic facts this decision established; linked automatically",
                    "items": {
                        "type": "object",
                        "properties": _FACT_PROPERTIES,
                        "required": ["subject", "predicate", "object"],
                    },
                },
                "decision_id": {
                    "type": "string",
                    "description": "Your own id for this decision (generated when omitted)",
                },
                "session_id": {"type": "string", "description": "This session's id (optional)"},
            },
            "required": ["title", "chosen", "rationale"],
        },
        "handler": tool_record_decision,
    },
    "memagora_facts_about": {
        "description": (
            "What does the team already know about this entity? Ask before assuming ownership, "
            "conventions, or contracts — the answer may predate this engineer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity to ask about"},
                "as_of": {
                    "type": "string",
                    "description": "ISO date — what was true then, rather than now (optional)",
                },
                "current": {
                    "type": "boolean",
                    "description": "Only facts that still hold (default true)",
                },
                "limit": {"type": "integer", "description": "Max facts (default 25)"},
            },
            "required": ["subject"],
        },
        "handler": tool_facts_about,
    },
    "memagora_timeline": {
        "description": (
            "How the team's knowledge changed over time, oldest first. Use to see what was "
            "superseded and when."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Narrow to one entity; matches either end of the relationship",
                },
                "limit": {"type": "integer", "description": "Max entries (default 25)"},
            },
        },
        "handler": tool_timeline,
    },
    "memagora_decisions_about": {
        "description": (
            "The recorded decisions behind what the team knows about an entity — chosen "
            "approach, rationale, alternatives rejected, open questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity to ask about"},
                "limit": {"type": "integer", "description": "Max decisions (default 10)"},
            },
            "required": ["subject"],
        },
        "handler": tool_decisions_about,
    },
    "memagora_why": {
        "description": (
            "Why does this specific relationship hold? Give a subject and predicate and get the "
            "current fact plus the decision behind it. Ask this before changing something the "
            "team decided deliberately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity"},
                "predicate": {"type": "string", "description": "The relationship, e.g. 'uses'"},
            },
            "required": ["subject", "predicate"],
        },
        "handler": tool_why,
    },
}


# ── Session start ───────────────────────────────────────────────────────


def team_context(*, wing: Optional[str] = None, limit: int = 10) -> Optional[str]:
    """Team knowledge to show at session start, or ``None`` when unavailable.

    **How the two structures are mapped.** The palace organizes by wing / room
    / drawer; the agora stores subject / predicate / object. Nothing links them
    intrinsically, so this uses the only honest correspondence available: a
    wing name is matched against the fact *subject* verbatim. That surfaces
    facts recorded about the project itself, and nothing else.

    Because that mapping is narrow — a fact about a service *inside* the
    project has that service as its subject, not the wing — a second, unscoped
    block of the most recently recorded facts is included as well. Between the
    two, a session opens knowing what the team decided about this project and
    what the team has been deciding lately.

    "Time-bounded" is by count: the agora returns facts newest-ingest-first, so
    the most recent ``limit`` is the window. There is no age cutoff, because a
    decision from last year is not less true than one from last week.

    Returns ``None`` when no agora is configured or it cannot be reached —
    wake-up must never fail because a team server is down.
    """
    cfg = load_agora_config()
    if not cfg.enabled:
        return None

    from .client import get_facts

    def _fetch(**kwargs):
        return get_facts(
            endpoint=cfg.endpoint,
            api_key=cfg.api_key,
            current=True,
            limit=limit,
            timeout=cfg.post_timeout,
            **kwargs,
        )

    about_wing = _fetch(subject=wing) if wing else None
    recent = _fetch()
    if about_wing is None and recent is None:
        logger.warning(
            "agora unreachable at %s; wake-up continues without team context", cfg.endpoint
        )
        return None

    sections = []
    seen = set()

    if about_wing and about_wing.facts:
        lines = []
        for fact in about_wing.facts:
            seen.add((fact.subject, fact.predicate, fact.object))
            lines.append(f"  {_render_fact(fact)}")
        sections.append(f"About {wing}:\n" + "\n".join(lines))

    if recent and recent.facts:
        lines = [
            f"  {_render_fact(fact)}"
            for fact in recent.facts
            if (fact.subject, fact.predicate, fact.object) not in seen
        ]
        if lines:
            sections.append("Recently from the team:\n" + "\n".join(lines))

    if not sections:
        return None

    header = f"TEAM AGORA ({cfg.endpoint})"
    footer = (
        "Ask memagora_why(subject, predicate) before changing anything above — "
        "the reasoning may be recorded."
    )
    return f"{header}\n" + "\n\n".join(sections) + f"\n\n{footer}"
