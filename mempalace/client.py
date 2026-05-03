"""HTTP client for posting classified facts to a team agora server.

v0.1 stub: returns ``True`` without making any network call. The real
HTTP implementation lands in v0.2 along with the choice of HTTP library
(see ROADMAP.md). Until then, this module deliberately imports nothing
from ``httpx`` / ``requests`` / etc. — keeping the engineer-side
install free of network deps.

The "no silent network calls" principle (see AGENTS.md) means the only
network call MemAgora ever makes is the explicit POST in this module.
v0.1 enforces that vacuously by making no calls at all.
"""

from typing import Optional

from contracts import FactPayload, PostFactsResponse


def post_facts(
    facts: list[FactPayload],
    *,
    endpoint: str,
    api_key: Optional[str] = None,
) -> PostFactsResponse:
    """POST classified facts to the agora server (v0.1 stub).

    Args:
        facts: Classified facts to send. v0.1 stub accepts any list.
        endpoint: Configured agora server URL (e.g., ``https://team.example/agora``).
        api_key: Optional API key for the deployment. v0.1 stub ignores it.

    Returns:
        ``PostFactsResponse`` with ``accepted == len(facts)``,
        ``rejected == 0``, and a stub message. v0.2 replaces this with
        a real HTTP round-trip and surfaces server-reported counts.

    Notes:
        v0.1 makes no network call. Callers can rely on this returning
        synchronously and never raising network errors. v0.2 will
        introduce an HTTP library, real timeout/retry behavior, and
        real response parsing — at which point this signature may grow
        a ``timeout=`` kwarg.
    """
    # v0.1: no httpx/requests import here, no network call. Return a
    # stub response so backend_agora can still exercise the success
    # path during dry-run testing.
    return PostFactsResponse(
        accepted=len(facts),
        rejected=0,
        message="v0.1 stub — no network call made",
    )
