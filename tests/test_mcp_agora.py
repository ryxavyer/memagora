"""MemAgora MCP tools — emission and query.

The network layer is stubbed at ``mempalace.client``; what these tests pin is
the behavior an agent depends on: dry-run never sends and says so, every
emission is mirrored to the audit log before anything crosses, provenance is
never invented, and a failure is reported rather than raised.
"""

import pytest

from contracts import DecisionRecord, FactPayload, GetDecisionsResponse, GetFactsResponse
from mempalace import audit as audit_module
from mempalace import mcp_agora


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: log_path)
    return log_path


@pytest.fixture
def configured(monkeypatch):
    """An agora endpoint with dry-run OFF."""
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://agora.test.example")
    monkeypatch.setenv("MEMPALACE_AGORA_API_KEY", "ak_1.supersecret")
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", "0")


@pytest.fixture
def dry_run(monkeypatch):
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://agora.test.example")
    monkeypatch.setenv("MEMPALACE_AGORA_API_KEY", "ak_1.supersecret")
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", "1")


@pytest.fixture
def unconfigured(monkeypatch):
    for var in ("MEMPALACE_AGORA_ENDPOINT", "MEMPALACE_AGORA_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Capture what would be ingested; report everything accepted."""
    from contracts import IngestResponse

    calls = []

    def fake_ingest(
        *, endpoint, facts=None, decisions=None, closes=None, api_key=None, timeout=None
    ):
        calls.append(
            {
                "endpoint": endpoint,
                "facts": list(facts or []),
                "decisions": list(decisions or []),
                "closes": list(closes or []),
                "api_key": api_key,
            }
        )
        return IngestResponse(
            facts_accepted=len(facts or []),
            facts_rejected=0,
            decisions_accepted=len(decisions or []),
            decisions_rejected=0,
            facts_closed=len(closes or []),
        )

    monkeypatch.setattr("mempalace.client.post_ingest", fake_ingest)
    return calls


def entries(path):
    return audit_module.read_audit_entries(path)


# ── Not configured ──────────────────────────────────────────────────────


def test_record_fact_without_an_endpoint_explains_itself(unconfigured, audit_log):
    result = mcp_agora.tool_record_fact(subject="api", predicate="owned_by", object="platform")
    assert result["success"] is False
    assert result["sent"] is False
    assert "MEMPALACE_AGORA_ENDPOINT" in result["error"]
    assert entries(audit_log) == []


def test_query_without_an_endpoint_explains_itself(unconfigured):
    assert mcp_agora.tool_facts_about(subject="api")["success"] is False
    assert mcp_agora.tool_why(subject="api", predicate="uses")["success"] is False


# ── Dry run: the default ────────────────────────────────────────────────


def test_dry_run_records_locally_and_sends_nothing(dry_run, audit_log, sent):
    result = mcp_agora.tool_record_fact(
        subject="api", predicate="owned_by", object="platform team", session_id="s-1"
    )

    assert sent == []  # nothing crossed the boundary
    assert result["success"] is True
    assert result["sent"] is False
    assert "NOT sent" in result["message"]

    logged = entries(audit_log)
    assert [e["entry_type"] for e in logged] == ["emit"]
    assert logged[0]["fact"]["subject"] == "api"
    assert logged[0]["dry_run"] is True


def test_dry_run_decision_records_locally_and_sends_nothing(dry_run, audit_log, sent):
    result = mcp_agora.tool_record_decision(
        title="Queue choice",
        chosen="SQS FIFO",
        rationale="Ordering is required.",
        facts=[{"subject": "notifications", "predicate": "uses", "object": "SQS FIFO"}],
    )
    assert sent == []
    assert result["sent"] is False
    assert result["facts_recorded"] == 1
    assert "NOT sent" in result["message"]

    logged = entries(audit_log)
    assert [e["entry_type"] for e in logged] == ["emit", "emit"]
    assert logged[0]["decision"]["title"] == "Queue choice"
    assert logged[1]["fact"]["subject"] == "notifications"


# ── Emission ────────────────────────────────────────────────────────────


def test_record_fact_sends_and_reports_where(configured, audit_log, sent):
    result = mcp_agora.tool_record_fact(
        subject="  api  ", predicate="owned_by", object="platform", confidence=0.9
    )
    assert result["success"] is True
    assert result["sent"] is True
    assert "agora.test.example" in result["message"]

    assert len(sent) == 1
    fact = sent[0]["facts"][0]
    assert isinstance(fact, FactPayload)
    assert fact.subject == "api"  # trimmed
    assert fact.confidence == 0.9
    assert sent[0]["api_key"] == "ak_1.supersecret"


def test_record_decision_links_its_facts(configured, audit_log, sent):
    result = mcp_agora.tool_record_decision(
        title="Queue for notifications",
        chosen="SQS FIFO",
        rationale="Per-recipient ordering is required.",
        alternatives_rejected=["Kafka — too much operational surface"],
        constraints=["Stay inside the current AWS account"],
        open_questions=["DLQ before launch?"],
        decided_on="2026-08-01",
        facts=[
            {"subject": "notifications", "predicate": "uses", "object": "SQS FIFO"},
            {"subject": "notifications", "predicate": "owned_by", "object": "platform"},
        ],
    )
    assert result["success"] is True
    decision = sent[0]["decisions"][0]
    assert isinstance(decision, DecisionRecord)
    assert decision.alternatives_rejected == ["Kafka — too much operational surface"]
    assert decision.decision_id == result["decision_id"]
    assert {f.decision_id for f in sent[0]["facts"]} == {result["decision_id"]}


def test_decision_id_is_generated_when_omitted_and_kept_when_given(configured, audit_log, sent):
    generated = mcp_agora.tool_record_decision(title="t", chosen="c", rationale="r")
    assert generated["decision_id"].startswith("dec_")

    given = mcp_agora.tool_record_decision(
        title="t", chosen="c", rationale="r", decision_id="dec-mine"
    )
    assert given["decision_id"] == "dec-mine"


def test_emission_is_audited_before_it_is_sent(configured, audit_log, sent):
    mcp_agora.tool_record_fact(subject="api", predicate="uses", object="SQS", session_id="s-9")
    logged = entries(audit_log)
    assert [e["entry_type"] for e in logged] == ["emit", "post"]
    assert logged[0]["dry_run"] is False
    assert logged[1]["ok"] is True
    assert logged[1]["endpoint"] == "https://agora.test.example"


def test_the_api_key_never_reaches_the_audit_log(configured, audit_log, sent):
    mcp_agora.tool_record_decision(
        title="t",
        chosen="c",
        rationale="r",
        facts=[{"subject": "a", "predicate": "b", "object": "c"}],
    )
    assert "supersecret" not in audit_log.read_text()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subject": "", "predicate": "p", "object": "o"},
        {"subject": "a", "predicate": "  ", "object": "o"},
        {"subject": "a", "predicate": "p", "object": ""},
    ],
)
def test_empty_triple_parts_are_refused_before_sending(configured, audit_log, sent, kwargs):
    result = mcp_agora.tool_record_fact(**kwargs)
    assert result["success"] is False
    assert sent == []


@pytest.mark.parametrize("missing", ["title", "chosen", "rationale"])
def test_a_decision_needs_its_reasoning(configured, audit_log, sent, missing):
    kwargs = {"title": "t", "chosen": "c", "rationale": "r"}
    kwargs[missing] = "   "
    result = mcp_agora.tool_record_decision(**kwargs)
    assert result["success"] is False
    assert missing in result["error"]
    assert sent == []


def test_malformed_inline_facts_are_refused_whole(configured, audit_log, sent):
    result = mcp_agora.tool_record_decision(
        title="t", chosen="c", rationale="r", facts=[{"subject": "a", "predicate": "b"}]
    )
    assert result["success"] is False
    assert sent == []
    assert entries(audit_log) == []  # nothing recorded either


def test_confidence_is_clamped(configured, audit_log, sent):
    mcp_agora.tool_record_fact(subject="a", predicate="b", object="c", confidence=7)
    assert sent[0]["facts"][0].confidence == 1.0


def test_a_rejected_fact_is_reported_not_raised(configured, audit_log, monkeypatch):
    from contracts import IngestResponse

    monkeypatch.setattr(
        "mempalace.client.post_ingest",
        lambda **kw: IngestResponse(
            facts_accepted=0,
            facts_rejected=1,
            decisions_accepted=0,
            decisions_rejected=0,
            message="rejected — duplicate_open_triple: 1",
        ),
    )
    result = mcp_agora.tool_record_fact(subject="a", predicate="b", object="c")
    assert result["success"] is False
    assert "duplicate_open_triple" in result["error"]
    assert entries(audit_log)[-1]["ok"] is False


def test_a_raising_client_never_escapes_the_tool(configured, audit_log, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("mempalace.client.post_ingest", boom)
    result = mcp_agora.tool_record_fact(subject="a", predicate="b", object="c")
    assert result["success"] is False
    assert "client error" in result["error"]


# ── Query ───────────────────────────────────────────────────────────────


def a_fact(subject="api", predicate="owned_by", obj="platform", **kwargs):
    return FactPayload(subject=subject, predicate=predicate, object=obj, **kwargs)


def test_facts_about_returns_rendered_facts(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(facts=[a_fact(), a_fact(predicate="uses", obj="SQS")]),
    )
    result = mcp_agora.tool_facts_about(subject="api")
    assert result["count"] == 2
    assert "api --owned_by--> platform" in result["facts"]
    assert "conflicts" not in result


def test_facts_about_flags_contradictory_open_facts(configured, monkeypatch):
    """Two open facts for the same subject+predicate is the superseding gap."""
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(
            facts=[
                a_fact(predicate="uses", obj="SQS FIFO"),
                a_fact(predicate="uses", obj="Kinesis"),
            ]
        ),
    )
    result = mcp_agora.tool_facts_about(subject="api")
    assert result["conflicts"] == [
        {"subject": "api", "predicate": "uses", "objects": ["SQS FIFO", "Kinesis"]}
    ]
    assert "superseded" in result["warning"]


def test_a_closed_fact_is_not_a_conflict(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(
            facts=[
                a_fact(predicate="uses", obj="SQS FIFO", valid_to="2026-06-01"),
                a_fact(predicate="uses", obj="Kinesis"),
            ]
        ),
    )
    assert "conflicts" not in mcp_agora.tool_facts_about(subject="api")


def test_unreachable_agora_is_reported_not_raised(configured, monkeypatch):
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: None)
    result = mcp_agora.tool_facts_about(subject="api")
    assert result["success"] is False
    assert "could not reach" in result["error"]


def test_timeline_marks_what_is_still_current(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_timeline",
        lambda **kw: GetFactsResponse(
            facts=[
                a_fact(obj="old-team", valid_from="2024-01-01", valid_to="2025-01-01"),
                a_fact(obj="platform", valid_from="2025-01-02"),
            ]
        ),
    )
    result = mcp_agora.tool_timeline(subject="api")
    assert [row["current"] for row in result["timeline"]] == [False, True]


def test_decisions_about_follows_the_link(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(facts=[a_fact(decision_id="dec_1")]),
    )
    monkeypatch.setattr(
        "mempalace.client.get_decisions",
        lambda **kw: GetDecisionsResponse(
            decisions=[
                DecisionRecord(
                    decision_id="dec_1",
                    title="Ownership",
                    chosen="platform",
                    rationale="They run the deploys.",
                    alternatives_rejected=["infra — already stretched"],
                )
            ]
        ),
    )
    result = mcp_agora.tool_decisions_about(subject="api")
    assert result["count"] == 1
    assert result["decisions"][0]["alternatives_rejected"] == ["infra — already stretched"]


def test_decisions_about_says_when_reasoning_was_never_recorded(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts", lambda **kw: GetFactsResponse(facts=[a_fact()])
    )
    result = mcp_agora.tool_decisions_about(subject="api")
    assert result["count"] == 0
    assert "nobody captured the reasoning" in result["message"]


def test_decisions_about_says_when_the_agora_is_empty(configured, monkeypatch):
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: GetFactsResponse(facts=[]))
    assert "holds nothing" in mcp_agora.tool_decisions_about(subject="api")["message"]


def test_why_returns_the_fact_and_its_decision(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(
            facts=[a_fact(predicate="uses", obj="SQS", decision_id="d1")]
        ),
    )
    monkeypatch.setattr(
        "mempalace.client.get_decisions",
        lambda **kw: GetDecisionsResponse(
            decisions=[
                DecisionRecord(decision_id="d1", title="Queue", chosen="SQS", rationale="Ordering.")
            ]
        ),
    )
    result = mcp_agora.tool_why(subject="api", predicate="uses")
    assert result["found"] is True
    assert result["decisions"][0]["rationale"] == "Ordering."


def test_why_reports_nothing_found(configured, monkeypatch):
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: GetFactsResponse(facts=[]))
    result = mcp_agora.tool_why(subject="api", predicate="uses")
    assert result["found"] is False


def test_why_warns_when_several_facts_are_open_at_once(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(
            facts=[a_fact(predicate="uses", obj="SQS"), a_fact(predicate="uses", obj="Kinesis")]
        ),
    )
    result = mcp_agora.tool_why(subject="api", predicate="uses")
    assert "contradict" in result["warning"]


def test_why_says_when_a_fact_has_no_recorded_reasoning(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts", lambda **kw: GetFactsResponse(facts=[a_fact()])
    )
    result = mcp_agora.tool_why(subject="api", predicate="owned_by")
    assert result["decisions"] == []
    assert "without its reasoning" in result["message"]


# ── Registry ────────────────────────────────────────────────────────────


def test_tools_are_registered_on_the_mcp_server():
    from mempalace.mcp_server import TOOLS

    for name in (
        "memagora_record_fact",
        "memagora_record_decision",
        "memagora_facts_about",
        "memagora_timeline",
        "memagora_decisions_about",
        "memagora_why",
    ):
        assert name in TOOLS
        assert TOOLS[name]["description"]
        assert TOOLS[name]["input_schema"]["type"] == "object"
        assert callable(TOOLS[name]["handler"])


def test_emission_tools_require_their_core_arguments():
    from mempalace.mcp_server import TOOLS

    assert TOOLS["memagora_record_fact"]["input_schema"]["required"] == [
        "subject",
        "predicate",
        "object",
    ]
    assert TOOLS["memagora_record_decision"]["input_schema"]["required"] == [
        "title",
        "chosen",
        "rationale",
    ]


# ── Superseding ─────────────────────────────────────────────────────────


def test_supersedes_closes_the_old_fact_in_the_same_request(configured, audit_log, sent):
    result = mcp_agora.tool_record_fact(
        subject="notifications",
        predicate="uses",
        object="Kinesis",
        supersedes="SQS FIFO",
        valid_from="2026-09-01",
    )
    assert result["success"] is True
    assert result["superseded"] is True
    assert "warning" not in result

    batch = sent[0]
    assert [f.object for f in batch["facts"]] == ["Kinesis"]
    close = batch["closes"][0]
    assert (close.subject, close.predicate, close.object) == ("notifications", "uses", "SQS FIFO")
    # The replacement's start is the old fact's end: no gap, no overlap.
    assert close.valid_to == "2026-09-01"


def test_superseding_nothing_warns_instead_of_pretending(configured, audit_log, monkeypatch):
    from contracts import IngestResponse

    monkeypatch.setattr(
        "mempalace.client.post_ingest",
        lambda **kw: IngestResponse(
            facts_accepted=1,
            facts_rejected=0,
            decisions_accepted=0,
            decisions_rejected=0,
            facts_closed=0,
        ),
    )
    result = mcp_agora.tool_record_fact(
        subject="api", predicate="uses", object="Kinesis", supersedes="SQS"
    )
    assert result["superseded"] is False
    assert "two current answers" in result["warning"]


def test_a_close_is_audited_like_anything_else_that_crosses(configured, audit_log, sent):
    mcp_agora.tool_record_fact(
        subject="api", predicate="uses", object="Kinesis", supersedes="SQS", session_id="s-2"
    )
    logged = entries(audit_log)
    ops = [e["op"] for e in logged]
    assert "close_fact" in ops
    close_entry = next(e for e in logged if e["op"] == "close_fact")
    assert close_entry["close"]["object"] == "SQS"
    # Closed before the replacement is written.
    assert ops.index("close_fact") < ops.index("record_fact")


def test_dry_run_does_not_close_anything_either(dry_run, audit_log, sent):
    result = mcp_agora.tool_record_fact(
        subject="api", predicate="uses", object="Kinesis", supersedes="SQS"
    )
    assert sent == []
    assert result["sent"] is False
    assert any(e["op"] == "close_fact" for e in entries(audit_log))


def test_a_decision_can_supersede_through_its_inline_facts(configured, audit_log, sent):
    result = mcp_agora.tool_record_decision(
        title="Move off SQS",
        chosen="Kinesis",
        rationale="We need replay, which FIFO does not give us.",
        facts=[
            {
                "subject": "notifications",
                "predicate": "uses",
                "object": "Kinesis",
                "supersedes": "SQS FIFO",
                "valid_from": "2026-09-01",
            }
        ],
    )
    assert result["facts_superseded"] == 1
    close = sent[0]["closes"][0]
    assert close.object == "SQS FIFO"
    assert close.valid_to == "2026-09-01"


def test_supersedes_is_advertised_to_the_agent():
    from mempalace.mcp_server import TOOLS

    schema = TOOLS["memagora_record_fact"]["input_schema"]["properties"]["supersedes"]
    assert "closes the old fact" in schema["description"].lower()


# ── Session start ───────────────────────────────────────────────────────


def test_team_context_is_none_without_an_agora(unconfigured):
    assert mcp_agora.team_context() is None


def test_team_context_is_none_when_the_agora_is_unreachable(configured, monkeypatch):
    """Wake-up must never fail because a team server is down."""
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: None)
    assert mcp_agora.team_context(wing="myapp") is None


def test_team_context_scopes_by_wing_name_and_adds_recent(configured, monkeypatch):
    def fake_get_facts(**kwargs):
        if kwargs.get("subject") == "myapp":
            return GetFactsResponse(facts=[a_fact(subject="myapp", obj="platform")])
        return GetFactsResponse(
            facts=[
                a_fact(subject="myapp", obj="platform"),  # duplicate of the wing block
                a_fact(subject="billing", predicate="uses", obj="Stripe"),
            ]
        )

    monkeypatch.setattr("mempalace.client.get_facts", fake_get_facts)
    text = mcp_agora.team_context(wing="myapp")

    assert "About myapp:" in text
    assert "Recently from the team:" in text
    # The wing fact appears once, not twice.
    assert text.count("myapp --owned_by--> platform") == 1
    assert "billing --uses--> Stripe" in text
    assert "memagora_why" in text


def test_team_context_without_a_wing_is_just_recent(configured, monkeypatch):
    monkeypatch.setattr(
        "mempalace.client.get_facts",
        lambda **kw: GetFactsResponse(facts=[a_fact(subject="billing")]),
    )
    text = mcp_agora.team_context()
    assert "About" not in text
    assert "Recently from the team:" in text


def test_team_context_is_none_when_the_agora_is_empty(configured, monkeypatch):
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: GetFactsResponse(facts=[]))
    assert mcp_agora.team_context(wing="myapp") is None


def test_wake_up_appends_team_context(monkeypatch, capsys):
    import argparse

    from mempalace import cli

    monkeypatch.setattr(
        "mempalace.layers.MemoryStack.wake_up", lambda self, wing=None: "PALACE CONTEXT"
    )
    monkeypatch.setattr("mempalace.mcp_agora.team_context", lambda **kw: "TEAM CONTEXT")

    cli.cmd_wakeup(argparse.Namespace(palace=None, wing="myapp", no_team=False))
    out = capsys.readouterr().out
    assert "PALACE CONTEXT" in out
    assert "TEAM CONTEXT" in out


def test_wake_up_survives_an_agora_that_returns_nothing(monkeypatch, capsys):
    import argparse

    from mempalace import cli

    monkeypatch.setattr(
        "mempalace.layers.MemoryStack.wake_up", lambda self, wing=None: "PALACE CONTEXT"
    )
    monkeypatch.setattr("mempalace.mcp_agora.team_context", lambda **kw: None)

    cli.cmd_wakeup(argparse.Namespace(palace=None, wing=None, no_team=False))
    assert "PALACE CONTEXT" in capsys.readouterr().out


def test_no_team_flag_skips_the_agora_entirely(monkeypatch, capsys):
    import argparse

    from mempalace import cli

    monkeypatch.setattr(
        "mempalace.layers.MemoryStack.wake_up", lambda self, wing=None: "PALACE CONTEXT"
    )

    def boom(**kwargs):
        raise AssertionError("team_context must not be called with --no-team")

    monkeypatch.setattr("mempalace.mcp_agora.team_context", boom)
    cli.cmd_wakeup(argparse.Namespace(palace=None, wing="myapp", no_team=True))
    assert "PALACE CONTEXT" in capsys.readouterr().out
