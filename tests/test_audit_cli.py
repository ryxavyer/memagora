"""Tests for mempalace.audit_cli — `mempalace audit tail` and `audit export`."""

import json
from pathlib import Path

import pytest

from mempalace import audit as audit_module
from mempalace.audit_cli import _format_entry, run_audit


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    """Seed an isolated audit log + redirect the module default to point at it."""
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: log_path)
    return log_path


def _seed(log_path: Path, entries: list[dict]) -> None:
    for entry in entries:
        audit_module.write_audit_entry(entry, audit_path=log_path)


# ── _format_entry ───────────────────────────────────────────────────────


def test_format_entry_classify():
    entry = {
        "entry_type": "classify",
        "op": "classified",
        "session_id": "s-7",
        "fact": {
            "subject": "project",
            "predicate": "uses",
            "object": "PostgreSQL",
            "confidence": 0.9,
        },
        "dry_run": False,
    }
    line = _format_entry(entry)
    assert "classify" in line
    assert "s-7" in line
    assert "project --uses--> PostgreSQL" in line
    assert "conf=0.9" in line
    assert "[dry-run]" not in line


def test_format_entry_classify_dry_run_prefixed():
    entry = {
        "entry_type": "classify",
        "session_id": "s-1",
        "fact": {"subject": "a", "predicate": "is", "object": "b", "confidence": 0.9},
        "dry_run": True,
    }
    line = _format_entry(entry)
    assert line.startswith("[dry-run]")


def test_format_entry_drawer_write():
    entry = {"entry_type": "drawer_write", "op": "add", "id": "fact-1", "dry_run": False}
    line = _format_entry(entry)
    assert "drawer" in line
    assert "[add]" in line
    assert "id=fact-1" in line


def test_format_entry_unknown_falls_back_to_json():
    entry = {"entry_type": "mystery", "op": "wat", "weird_field": 42}
    line = _format_entry(entry)
    assert "mystery" in line
    assert "weird_field" in line  # raw JSON shown


# ── run_audit dispatch ──────────────────────────────────────────────────


def test_run_audit_unknown_action_returns_2(capsys):
    # "diff" was the placeholder here until v0.3 implemented it.
    rc = run_audit(action="replay")
    assert rc == 2
    assert "action required" in capsys.readouterr().err


def test_run_audit_none_action_returns_2(capsys):
    rc = run_audit(action=None)
    assert rc == 2


# ── tail ────────────────────────────────────────────────────────────────


def test_tail_empty_log_prints_helpful_message(audit_log, capsys):
    rc = run_audit(action="tail", limit=10)
    assert rc == 0
    out = capsys.readouterr().out
    assert "empty" in out.lower()


def test_tail_shows_last_n_entries(audit_log, capsys):
    _seed(
        audit_log,
        [{"entry_type": "drawer_write", "op": "add", "id": f"id-{i}"} for i in range(20)],
    )
    rc = run_audit(action="tail", limit=3)
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 3
    assert "id-17" in lines[0]
    assert "id-19" in lines[2]


def test_tail_with_zero_or_negative_limit_shows_all(audit_log, capsys):
    _seed(
        audit_log,
        [{"entry_type": "drawer_write", "op": "add", "id": str(i)} for i in range(5)],
    )
    rc = run_audit(action="tail", limit=0)
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 5


def test_tail_mixed_entry_types(audit_log, capsys):
    _seed(
        audit_log,
        [
            {"entry_type": "drawer_write", "op": "add", "id": "fact-1"},
            {
                "entry_type": "classify",
                "op": "classified",
                "session_id": "s-1",
                "fact": {"subject": "x", "predicate": "is", "object": "y", "confidence": 0.9},
            },
        ],
    )
    rc = run_audit(action="tail", limit=10)
    assert rc == 0
    out = capsys.readouterr().out
    assert "drawer" in out
    assert "classify" in out


# ── export ──────────────────────────────────────────────────────────────


def test_export_to_stdout(audit_log, capsys):
    _seed(
        audit_log,
        [
            {"entry_type": "drawer_write", "op": "add", "id": "a"},
            {"entry_type": "drawer_write", "op": "add", "id": "b"},
        ],
    )
    rc = run_audit(action="export", output=None)
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line is valid JSON


def test_export_to_file(tmp_path, audit_log, capsys):
    _seed(
        audit_log,
        [
            {"entry_type": "drawer_write", "op": "add", "id": "a"},
            {"entry_type": "drawer_write", "op": "upsert", "id": "b"},
        ],
    )
    out_path = tmp_path / "subdir" / "export.jsonl"
    rc = run_audit(action="export", output=str(out_path))
    assert rc == 0

    # Subdirs are created on demand
    assert out_path.exists()
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "a"
    assert json.loads(lines[1])["id"] == "b"

    # Stdout reports the write
    stdout = capsys.readouterr().out
    assert "Wrote 2" in stdout


def test_export_empty_log_to_file(tmp_path, audit_log):
    """Exporting an empty audit log to a file creates the file but it's empty."""
    out_path = tmp_path / "export.jsonl"
    rc = run_audit(action="export", output=str(out_path))
    assert rc == 0
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == ""


# ── audit diff ──────────────────────────────────────────────────────────


@pytest.fixture
def agora_config(monkeypatch):
    """Configure an endpoint so diff has something to talk to."""
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://agora.test.example")
    monkeypatch.setenv("MEMPALACE_AGORA_API_KEY", "ak_1.secret")
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", "0")


def _classify_entry(subject, predicate="uses", obj="PostgreSQL"):
    return {
        "entry_type": "classify",
        "op": "classified",
        "session_id": "s-1",
        "fact": {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "confidence": 0.9,
        },
        "dry_run": False,
    }


def _serve(monkeypatch, triples, *, pages=None):
    """Stub client.get_facts with a canned server response."""
    from contracts import FactPayload, GetFactsResponse

    def fake_get_facts(**kwargs):
        if pages is not None:
            return pages.pop(0)
        return GetFactsResponse(
            facts=[FactPayload(subject=s, predicate=p, object=o) for s, p, o in triples],
            next_cursor=None,
        )

    monkeypatch.setattr("mempalace.client.get_facts", fake_get_facts)


def test_diff_without_an_endpoint_is_a_friendly_noop(audit_log, monkeypatch, capsys):
    monkeypatch.delenv("MEMPALACE_AGORA_ENDPOINT", raising=False)
    assert run_audit(action="diff") == 0
    assert "No agora endpoint configured" in capsys.readouterr().out


def test_diff_reports_matches(audit_log, agora_config, monkeypatch, capsys):
    _seed(audit_log, [_classify_entry("project")])
    _serve(monkeypatch, [("project", "uses", "PostgreSQL")])

    assert run_audit(action="diff") == 0
    out = capsys.readouterr().out
    assert "matched         : 1" in out
    assert "local only      : 0" in out


def test_diff_reports_local_only_facts(audit_log, agora_config, monkeypatch, capsys):
    _seed(audit_log, [_classify_entry("project"), _classify_entry("api", "owned_by", "platform")])
    _serve(monkeypatch, [("project", "uses", "PostgreSQL")])

    assert run_audit(action="diff") == 0
    out = capsys.readouterr().out
    assert "local only      : 1" in out
    assert "api --owned_by--> platform" in out


def test_diff_reports_teammates_facts_as_agora_only(audit_log, agora_config, monkeypatch, capsys):
    _seed(audit_log, [_classify_entry("project")])
    _serve(monkeypatch, [("project", "uses", "PostgreSQL"), ("web", "owned_by", "frontend")])

    assert run_audit(action="diff") == 0
    out = capsys.readouterr().out
    assert "agora only      : 1" in out
    assert "web --owned_by--> frontend" in out


def test_diff_normalizes_predicates_before_comparing(audit_log, agora_config, monkeypatch, capsys):
    """The server lowercases and underscores predicates; the local log holds
    whatever the classifier emitted. Comparing raw would report false gaps."""
    _seed(audit_log, [_classify_entry("api", "Owned By", "platform")])
    _serve(monkeypatch, [("api", "owned_by", "platform")])

    run_audit(action="diff")
    assert "matched         : 1" in capsys.readouterr().out


def test_diff_ignores_non_classify_entries(audit_log, agora_config, monkeypatch, capsys):
    _seed(
        audit_log,
        [
            {"entry_type": "drawer_write", "op": "add", "id": "d1", "dry_run": False},
            {"entry_type": "post", "op": "posted", "endpoint": "x", "accepted": 1, "ok": True},
            _classify_entry("project"),
        ],
    )
    _serve(monkeypatch, [("project", "uses", "PostgreSQL")])

    run_audit(action="diff")
    out = capsys.readouterr().out
    assert "matched         : 1" in out
    assert "local only      : 0" in out


def test_diff_deduplicates_repeated_classifications(audit_log, agora_config, monkeypatch, capsys):
    """The same fact classified in three sessions is one fact in the agora."""
    _seed(audit_log, [_classify_entry("project")] * 3)
    _serve(monkeypatch, [("project", "uses", "PostgreSQL")])

    run_audit(action="diff")
    assert "matched         : 1" in capsys.readouterr().out


def test_diff_follows_pagination(audit_log, agora_config, monkeypatch, capsys):
    from contracts import FactPayload, GetFactsResponse

    pages = [
        GetFactsResponse(
            facts=[FactPayload(subject="project", predicate="uses", object="PostgreSQL")],
            next_cursor="page-2",
        ),
        GetFactsResponse(
            facts=[FactPayload(subject="web", predicate="owned_by", object="frontend")],
            next_cursor=None,
        ),
    ]
    _serve(monkeypatch, None, pages=pages)
    _seed(audit_log, [_classify_entry("project")])

    run_audit(action="diff")
    out = capsys.readouterr().out
    assert "matched         : 1" in out
    assert "agora only      : 1" in out


def test_diff_exit_code_2_when_the_agora_is_unreachable(
    audit_log, agora_config, monkeypatch, capsys
):
    monkeypatch.setattr("mempalace.client.get_facts", lambda **kw: None)
    assert run_audit(action="diff") == 2
    assert "could not reach" in capsys.readouterr().err


def test_diff_strict_exits_nonzero_on_local_only(audit_log, agora_config, monkeypatch):
    _seed(audit_log, [_classify_entry("project")])
    _serve(monkeypatch, [])
    assert run_audit(action="diff", strict=True) == 1
    assert run_audit(action="diff", strict=False) == 0


def test_diff_notes_dry_run_mode(audit_log, monkeypatch, capsys):
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://agora.test.example")
    monkeypatch.setenv("MEMPALACE_AGORA_DRY_RUN", "1")
    _seed(audit_log, [_classify_entry("project")])
    _serve(monkeypatch, [])

    run_audit(action="diff")
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "local only      : 1" in out


def test_unknown_action_mentions_diff(capsys):
    assert run_audit(action="bogus") == 2
    assert "diff" in capsys.readouterr().err


def test_format_entry_post():
    line = _format_entry(
        {
            "entry_type": "post",
            "session_id": "s-9",
            "endpoint": "https://agora.example",
            "fact_count": 2,
            "accepted": 2,
            "rejected": 0,
            "message": None,
            "ok": True,
        }
    )
    assert "post" in line and "s-9" in line and "accepted=2" in line


def test_format_entry_failed_post_is_visible():
    line = _format_entry(
        {
            "entry_type": "post",
            "session_id": "s-9",
            "endpoint": "https://agora.example",
            "accepted": 0,
            "rejected": 3,
            "message": "cannot reach host",
            "ok": False,
        }
    )
    assert "FAILED" in line
    assert "cannot reach host" in line
