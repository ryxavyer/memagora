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
    rc = run_audit(action="diff")  # diff is v0.3, not v0.2
    assert rc == 2
    err = capsys.readouterr().err
    assert "action required" in err or "diff" not in err


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
