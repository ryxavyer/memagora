"""Classifier eval harness — fixture conversations + expected facts.

Two layers:

1. **Mocked-LLM tests (default)** — each fixture has a hand-written
   ``llm_response`` payload that simulates what a well-behaved classifier
   should return for that conversation. We verify the parsing and
   FactPayload construction end-to-end. Runs in CI; fast; deterministic.

2. **Live-LLM tests (`@pytest.mark.live`)** — same fixtures, but call a
   real LLM via the default Anthropic provider. Verifies that the
   shipped prompt actually produces sane output. Gated by the ``live``
   marker (skipped by default in CI; run with ``pytest -m live``).
   Requires ``ANTHROPIC_API_KEY``.

When iterating the default prompt, run the live tests and watch the
diff. The mocked layer guards the plumbing; the live layer guards the
prompt quality.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

import pytest

from contracts import FactPayload
from mempalace.classifier import classify_text
from mempalace.config_agora import AgoraConfig


# ── Fixture format ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClassifierFixture:
    """A single classifier eval fixture.

    ``llm_response`` is the canned JSON the mocked LLM returns. Live
    tests ignore it and call the real model; comparison happens via
    ``expected_subjects`` (which works for both layers).
    """

    name: str
    transcript: str
    llm_response: list[dict]
    expected_subjects: set[str] = field(default_factory=set)
    expected_count: Optional[int] = None  # use None to skip exact-count check


FIXTURES: list[ClassifierFixture] = [
    ClassifierFixture(
        name="explicit_decision_postgres",
        transcript=(
            "user: Quick update — we're standardizing on PostgreSQL for all new services "
            "starting next quarter. Existing SQLite services will be migrated.\n"
            "assistant: Got it. Should I update the architecture docs?\n"
            "user: Yes."
        ),
        llm_response=[
            {
                "subject": "new_services",
                "predicate": "uses",
                "object": "PostgreSQL",
                "confidence": 0.95,
            },
            {
                "subject": "SQLite",
                "predicate": "deprecated_in_favor_of",
                "object": "PostgreSQL",
                "confidence": 0.9,
            },
        ],
        expected_subjects={"new_services", "SQLite"},
        expected_count=2,
    ),
    ClassifierFixture(
        name="exploratory_no_decision",
        transcript=(
            "user: Hey, what about switching to Redis?\n"
            "assistant: Trade-offs are X, Y, Z. SQLite is simpler operationally.\n"
            "user: Yeah, not sure. Let me think about it."
        ),
        llm_response=[],
        expected_subjects=set(),
        expected_count=0,
    ),
    ClassifierFixture(
        name="ownership_assignment",
        transcript=(
            "user: Alice owns the auth service from now on. She'll be the on-call for it.\n"
            "assistant: Noted."
        ),
        llm_response=[
            {
                "subject": "Alice",
                "predicate": "owns",
                "object": "auth_service",
                "confidence": 0.9,
            },
        ],
        expected_subjects={"Alice"},
        expected_count=1,
    ),
    ClassifierFixture(
        name="sla_contract",
        transcript=(
            "user: Decision: API p99 latency budget is 100ms. Anything slower needs a writeup.\n"
            "assistant: Got it."
        ),
        llm_response=[
            {
                "subject": "api",
                "predicate": "p99_latency_budget",
                "object": "100ms",
                "confidence": 0.92,
            },
        ],
        expected_subjects={"api"},
        expected_count=1,
    ),
    ClassifierFixture(
        name="debugging_without_resolution",
        transcript=(
            "user: I'm seeing 500s on /users. Stack trace is in the logs.\n"
            "assistant: Looks like the DB connection pool is exhausted.\n"
            "user: Yeah. I'll dig in tomorrow."
        ),
        llm_response=[],
        expected_subjects=set(),
        expected_count=0,
    ),
    ClassifierFixture(
        name="hypothetical_not_a_fact",
        transcript=(
            "user: If we used Kafka instead of SQS, what would the cost look like?\n"
            "assistant: Roughly 3x at our volume.\n"
            "user: Probably not worth it then."
        ),
        llm_response=[],
        expected_subjects=set(),
        expected_count=0,
    ),
    ClassifierFixture(
        name="low_confidence_dropped",
        transcript=(
            "user: I think maybe we should consider Postgres? Not sure yet.\n"
            "assistant: Worth exploring."
        ),
        # LLM emits one low-confidence fact — classifier MUST drop it.
        llm_response=[
            {
                "subject": "project",
                "predicate": "considering",
                "object": "PostgreSQL",
                "confidence": 0.4,
            },
        ],
        expected_subjects=set(),
        expected_count=0,
    ),
]


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_mocked_provider(response_json: list[dict]):
    """Build a MagicMock provider whose .classify(...) returns canned JSON."""
    provider = MagicMock()
    provider.classify.return_value = MagicMock(text=json.dumps(response_json))
    return provider


def _baseline_config() -> AgoraConfig:
    """An AgoraConfig with all the fields the classifier needs, no env-var lookups."""
    return AgoraConfig(
        endpoint="https://test.example/agora",
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        llm_api_key="test-key",
        max_facts_per_turn=10,
        transcript_last_n=30,
    )


# ── Mocked-LLM eval (default) ───────────────────────────────────────────


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_classifier_against_fixture_mocked(fixture: ClassifierFixture, monkeypatch):
    """Verify the classifier parses each fixture's canned LLM response correctly.

    This is a plumbing test — it asserts that *given* a well-formed LLM
    response, the classifier produces the right FactPayload list. It
    does NOT exercise prompt quality (that's the live test).
    """
    mocked = _make_mocked_provider(fixture.llm_response)
    monkeypatch.setattr("mempalace.classifier.get_provider", lambda **kw: mocked)

    facts = classify_text(fixture.transcript, config=_baseline_config())

    if fixture.expected_count is not None:
        assert len(facts) == fixture.expected_count, (
            f"{fixture.name}: expected {fixture.expected_count} fact(s), got {len(facts)}"
        )

    actual_subjects = {f.subject for f in facts}
    assert actual_subjects == fixture.expected_subjects, (
        f"{fixture.name}: expected subjects {fixture.expected_subjects}, got {actual_subjects}"
    )

    # Every emitted fact is a FactPayload (no leaked dicts)
    for f in facts:
        assert isinstance(f, FactPayload)


# ── Live-LLM eval (opt-in) ──────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_classifier_against_fixture_live(fixture: ClassifierFixture):
    """Real LLM call — gated by ``live`` marker AND ANTHROPIC_API_KEY.

    Run with::

        ANTHROPIC_API_KEY=... uv run pytest -m live -v tests/test_classifier_eval.py

    Engineers iterate the default prompt by watching this test's pass
    rate. A failure means either the fixture's expected output is wrong
    OR the prompt needs tightening — both are useful signals.

    Use ``expected_subjects`` instead of exact match: live LLMs may
    legitimately split or merge facts in ways that pass the spirit of
    the test even if subjects differ slightly.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; live eval skipped")

    cfg = AgoraConfig(
        endpoint="https://test.example/agora",
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        # llm_api_key unset — fall back to ANTHROPIC_API_KEY
        max_facts_per_turn=10,
        transcript_last_n=30,
    )

    facts = classify_text(fixture.transcript, config=cfg)

    # For empty-expected fixtures, the live LLM MUST emit nothing.
    if not fixture.expected_subjects:
        assert facts == [], (
            f"{fixture.name}: expected no facts, got {[f.subject for f in facts]}. "
            "Prompt is being too eager."
        )
        return

    # For non-empty fixtures, accept the result if it covers at least
    # one of the expected subjects (subset semantics). Live LLMs may
    # phrase subjects differently — "Alice" vs "alice" vs "Alice (Eng)".
    actual = {f.subject.lower() for f in facts}
    expected_lower = {s.lower() for s in fixture.expected_subjects}
    overlap = actual & expected_lower
    assert overlap, (
        f"{fixture.name}: live output {actual} did not overlap with expected "
        f"{expected_lower}. Prompt may need iteration."
    )
