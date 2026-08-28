"""HTTP client for the team agora server.

This module owns the *only* network call MemAgora ever makes (see AGENTS.md,
"No silent network calls"). It talks to the endpoint the engineer configured
and nowhere else: no telemetry, no analytics, no fallback host.

Implemented on stdlib ``urllib`` — the same choice ``llm_client.py`` made — so
the engineer-side install stays free of ``httpx`` / ``requests``. The helper
here is deliberately local rather than reused from ``llm_client``: that one is
private to the LLM providers, raises ``LLMError``, and importing it would drag
the provider stack into the audit path.

**Nothing in this module raises.** It is called from a Claude Code hook, and a
hook that raises is a hook that breaks an engineer's session. Every failure —
unreachable host, timeout, 500, unparseable body — comes back as a
``PostFactsResponse`` reporting the batch as rejected, which the caller records
in the audit log.
"""

import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from contracts import (
    SCHEMA_VERSION,
    DecisionRecord,
    FactPayload,
    GetDecisionsResponse,
    GetFactsResponse,
    IngestResponse,
    PostFactsResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
USER_AGENT = "memagora-palace"

# One retry, on transport failures and 5xx only. A 4xx is a disagreement about
# the request itself and will fail identically the second time.
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS = 2


def post_facts(
    facts: list,
    *,
    endpoint: str,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> PostFactsResponse:
    """POST classified facts to the agora server.

    Args:
        facts: ``FactPayload`` objects from the classifier.
        endpoint: The engineer's configured agora URL.
        api_key: Bearer credential. The server derives the deployment and the
            engineer identity from it — they are never sent in the body.
        timeout: Per-attempt socket timeout in seconds. Bounded because the
            precompact hook calls this synchronously.

    Returns:
        The server's counts, or a locally-constructed response reporting the
        whole batch rejected when the call could not be completed.
    """
    if not facts:
        return PostFactsResponse(accepted=0, rejected=0, message=None)

    error = _validate_endpoint(endpoint)
    if error:
        return PostFactsResponse(accepted=0, rejected=len(facts), message=error)

    body = {
        "facts": [_as_dict(fact) for fact in facts],
        "schema_version": SCHEMA_VERSION,
    }

    payload, failure = _request(
        "POST", _url(endpoint, "/facts"), body=body, api_key=api_key, timeout=timeout
    )
    if failure is not None:
        logger.warning("agora POST failed: %s", failure)
        return PostFactsResponse(accepted=0, rejected=len(facts), message=failure)

    # Trust the server's own counts over our optimism about what it did.
    return PostFactsResponse(
        accepted=int(payload.get("accepted", 0)),
        rejected=int(payload.get("rejected", 0)),
        message=payload.get("message"),
    )


def post_ingest(
    *,
    endpoint: str,
    facts: Optional[list] = None,
    decisions: Optional[list] = None,
    closes: Optional[list] = None,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> IngestResponse:
    """POST a mixed batch of decisions and facts to ``/ingest``.

    The path agent-driven emission uses. Decisions and facts travel together so
    the server stores the decision first and a fact's ``decision_id`` never
    points at something that is not there yet.

    Never raises, exactly as :func:`post_facts` does not — an emission tool
    that threw would surface as a tool error inside the agent's session.
    """
    facts = list(facts or [])
    decisions = list(decisions or [])
    closes = list(closes or [])
    if not facts and not decisions and not closes:
        return IngestResponse(
            facts_accepted=0, facts_rejected=0, decisions_accepted=0, decisions_rejected=0
        )

    error = _validate_endpoint(endpoint)
    if error:
        return IngestResponse(
            facts_accepted=0,
            facts_rejected=len(facts),
            decisions_accepted=0,
            decisions_rejected=len(decisions),
            message=error,
        )

    body = {
        "facts": [_as_dict(fact) for fact in facts],
        "decisions": [_as_dict(decision) for decision in decisions],
        "closes": [_as_dict(close) for close in closes],
        "schema_version": SCHEMA_VERSION,
    }

    payload, failure = _request(
        "POST", _url(endpoint, "/ingest"), body=body, api_key=api_key, timeout=timeout
    )
    if failure is not None:
        logger.warning("agora ingest failed: %s", failure)
        return IngestResponse(
            facts_accepted=0,
            facts_rejected=len(facts),
            decisions_accepted=0,
            decisions_rejected=len(decisions),
            message=failure,
        )

    return IngestResponse(
        facts_accepted=int(payload.get("facts_accepted", 0)),
        facts_rejected=int(payload.get("facts_rejected", 0)),
        decisions_accepted=int(payload.get("decisions_accepted", 0)),
        decisions_rejected=int(payload.get("decisions_rejected", 0)),
        facts_closed=int(payload.get("facts_closed", 0)),
        message=payload.get("message"),
    )


def get_facts(
    *,
    endpoint: str,
    api_key: Optional[str] = None,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,
    as_of: Optional[str] = None,
    current: bool = False,
    min_confidence: Optional[float] = None,
    decision_id: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[GetFactsResponse]:
    """Fetch one page of facts. Returns ``None`` when the call fails.

    Every filter the server implements is exposed here. ``audit diff`` uses
    none of them; the MCP query tools use all of them, which is why v0.3's
    limit-and-cursor-only signature was not enough.
    """
    if _validate_endpoint(endpoint):
        return None

    params = {"limit": limit}
    for name, value in (
        ("subject", subject),
        ("predicate", predicate),
        ("object", object),
        ("as_of", as_of),
        ("decision_id", decision_id),
        ("cursor", cursor),
    ):
        if value:
            params[name] = value
    if current:
        params["current"] = "true"
    if min_confidence is not None:
        params["min_confidence"] = min_confidence

    payload, failure = _request(
        "GET",
        _url(endpoint, "/facts") + "?" + urlencode(params),
        api_key=api_key,
        timeout=timeout,
    )
    if failure is not None:
        logger.warning("agora GET /facts failed: %s", failure)
        return None

    facts = []
    for raw in payload.get("facts", []):
        fact = _fact_from_dict(raw)
        if fact is not None:
            facts.append(fact)
    return GetFactsResponse(facts=facts, next_cursor=payload.get("next_cursor"))


def get_timeline(
    *,
    endpoint: str,
    api_key: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[GetFactsResponse]:
    """Facts ordered by when they started holding. ``None`` on failure."""
    if _validate_endpoint(endpoint):
        return None

    params = {"limit": limit}
    if subject:
        params["subject"] = subject
    if cursor:
        params["cursor"] = cursor

    payload, failure = _request(
        "GET",
        _url(endpoint, "/timeline") + "?" + urlencode(params),
        api_key=api_key,
        timeout=timeout,
    )
    if failure is not None:
        logger.warning("agora GET /timeline failed: %s", failure)
        return None

    facts = [f for f in (_fact_from_dict(raw) for raw in payload.get("facts", [])) if f]
    return GetFactsResponse(facts=facts, next_cursor=payload.get("next_cursor"))


def get_decisions(
    *,
    endpoint: str,
    api_key: Optional[str] = None,
    ids: Optional[list] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[GetDecisionsResponse]:
    """Fetch decisions, optionally restricted to a set of ids.

    An empty ``ids`` list means "none of them" — never "all of them" — matching
    the rule the server and the storage layer both enforce.
    """
    if _validate_endpoint(endpoint):
        return None
    if ids is not None and not ids:
        return GetDecisionsResponse(decisions=[], next_cursor=None)

    params = {"limit": limit}
    if ids:
        params["ids"] = ",".join(ids)
    if cursor:
        params["cursor"] = cursor

    payload, failure = _request(
        "GET",
        _url(endpoint, "/decisions") + "?" + urlencode(params),
        api_key=api_key,
        timeout=timeout,
    )
    if failure is not None:
        logger.warning("agora GET /decisions failed: %s", failure)
        return None

    decisions = []
    for raw in payload.get("decisions", []):
        decision = _decision_from_dict(raw)
        if decision is not None:
            decisions.append(decision)
    return GetDecisionsResponse(decisions=decisions, next_cursor=payload.get("next_cursor"))


# ── Internals ───────────────────────────────────────────────────────────


def _validate_endpoint(endpoint: str) -> Optional[str]:
    """Return an error string when the endpoint is not one we will talk to."""
    if not endpoint:
        return "no agora endpoint configured"
    if not endpoint.startswith(("http://", "https://")):
        return f"refusing non-HTTP agora endpoint: {endpoint!r}"
    return None


def _url(endpoint: str, path: str) -> str:
    return endpoint.rstrip("/") + path


def _as_dict(fact) -> dict:
    """Serialize a fact for the wire.

    ``asdict`` rather than a hand-written field list: the list version silently
    dropped ``decision_id`` when the contract gained it in 0.2.0, and would do
    the same to the next field.
    """
    if is_dataclass(fact):
        return asdict(fact)
    return dict(fact)


def _fact_from_dict(raw: dict) -> Optional[FactPayload]:
    """Build a ``FactPayload`` from a server response row.

    Server responses carry additive fields (``fact_id``, ``engineer_id``, …)
    that the wire contract does not define; they are dropped here rather than
    causing a failure, which is the tolerance ``contracts/api.py`` asks for.
    """
    try:
        return FactPayload(
            subject=raw["subject"],
            predicate=raw["predicate"],
            object=raw["object"],
            valid_from=raw.get("valid_from"),
            valid_to=raw.get("valid_to"),
            confidence=float(raw.get("confidence", 1.0)),
            source_session_id=raw.get("source_session_id"),
            decision_id=raw.get("decision_id"),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("skipping malformed fact in agora response")
        return None


def _decision_from_dict(raw: dict) -> Optional[DecisionRecord]:
    """Build a ``DecisionRecord`` from a server response row.

    Additive server fields (``engineer_id``, ``recorded_at``) are dropped, as
    on the fact path.
    """
    try:
        return DecisionRecord(
            decision_id=raw["decision_id"],
            title=raw["title"],
            chosen=raw["chosen"],
            rationale=raw["rationale"],
            alternatives_rejected=list(raw.get("alternatives_rejected") or []),
            constraints=list(raw.get("constraints") or []),
            open_questions=list(raw.get("open_questions") or []),
            decided_on=raw.get("decided_on"),
            source_session_id=raw.get("source_session_id"),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("skipping malformed decision in agora response")
        return None


def _request(
    method: str,
    url: str,
    *,
    body: Optional[dict] = None,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[dict, Optional[str]]:
    """Perform one JSON request with a single retry. Never raises.

    Returns ``(payload, None)`` on success or ``({}, error_message)``.
    """
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    failure = "request not attempted"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")), None
        except HTTPError as exc:
            failure = f"HTTP {exc.code}: {_error_detail(exc)}"
            if exc.code not in _RETRYABLE_STATUS:
                return {}, failure
        except (URLError, OSError) as exc:
            # Includes socket.timeout, DNS failures, refused connections.
            failure = f"cannot reach {url}: {exc}"
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # A malformed body will be malformed again; do not retry.
            return {}, f"malformed response from {url}: {exc}"

        if attempt == _MAX_ATTEMPTS:
            break

    return {}, failure


def _error_detail(exc: HTTPError) -> str:
    """Pull the server's error message out of a non-2xx response."""
    try:
        payload: Any = json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:
        return exc.reason or "no detail"
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)[:300]
    return str(payload)[:300]
