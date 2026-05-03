"""Tests for mempalace.audit — append-only JSONL audit log."""

import json

import pytest

from mempalace.audit import read_audit_entries, write_audit_entry


def test_write_creates_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    assert not path.exists()
    write_audit_entry({"op": "add", "id": "fact-1"}, audit_path=path)
    assert path.exists()


def test_write_creates_parent_dirs(tmp_path):
    """Parent directory is created on first write — no manual mkdir needed."""
    path = tmp_path / "deeply" / "nested" / "audit.jsonl"
    write_audit_entry({"op": "add"}, audit_path=path)
    assert path.exists()


def test_write_appends(tmp_path):
    """Multiple writes accumulate, never overwrite."""
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"op": "add", "id": "fact-1"}, audit_path=path)
    write_audit_entry({"op": "upsert", "id": "fact-2"}, audit_path=path)
    write_audit_entry({"op": "add", "id": "fact-3"}, audit_path=path)

    entries = read_audit_entries(audit_path=path)
    assert len(entries) == 3
    assert entries[0]["id"] == "fact-1"
    assert entries[2]["id"] == "fact-3"


def test_each_line_is_valid_json(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"op": "add", "value": 42}, audit_path=path)
    write_audit_entry({"op": "add", "value": "string"}, audit_path=path)

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                json.loads(stripped)  # raises on malformed


def test_unicode_preserved(tmp_path):
    """Non-ASCII content is human-readable in the audit log, not \\u escaped."""
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"note": "résumé naïveté 漢字"}, audit_path=path)

    raw = path.read_text(encoding="utf-8")
    assert "résumé" in raw
    assert "漢字" in raw

    entries = read_audit_entries(audit_path=path)
    assert entries[0]["note"] == "résumé naïveté 漢字"


def test_idempotent_across_reopens(tmp_path):
    """Closing and reopening the same path mid-write doesn't corrupt the file."""
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"op": "add", "id": "1"}, audit_path=path)
    # Simulate a process boundary — same function called fresh.
    write_audit_entry({"op": "add", "id": "2"}, audit_path=path)

    entries = read_audit_entries(audit_path=path)
    assert [e["id"] for e in entries] == ["1", "2"]


def test_read_missing_file_returns_empty_list(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    assert read_audit_entries(audit_path=path) == []


def test_read_skips_blank_lines(tmp_path):
    """A trailing newline shouldn't produce a phantom entry."""
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"op": "add"}, audit_path=path)

    # Append a stray blank line — read must not raise.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n\n")

    entries = read_audit_entries(audit_path=path)
    assert len(entries) == 1


def test_entries_are_sorted_by_key(tmp_path):
    """JSON output uses sort_keys=True so logs diff stably across machines."""
    path = tmp_path / "audit.jsonl"
    write_audit_entry({"zebra": 1, "alpha": 2, "mango": 3}, audit_path=path)

    raw = path.read_text(encoding="utf-8").strip()
    # alpha < mango < zebra alphabetically
    assert raw.index('"alpha"') < raw.index('"mango"') < raw.index('"zebra"')


def test_default_audit_path_is_in_home(monkeypatch, tmp_path):
    """Default path resolves under $HOME/.mempalace/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads HOME on POSIX; on Windows it uses USERPROFILE.
    # This test is most useful on Linux/Mac CI; Windows may need its own.
    if pytest.importorskip("os").name == "nt":
        pytest.skip("HOME-based default-path test is POSIX-only")

    write_audit_entry({"op": "add"})

    expected = tmp_path / ".mempalace" / "audit.jsonl"
    assert expected.exists()
