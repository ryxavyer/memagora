"""Tests for mempalace.backend_agora — AgoraBackend / AgoraCollection.

Verifies:
- Wrapper passes every BaseCollection op through to the inner ChromaCollection
- add/upsert with endpoint=None (disabled) writes no audit entries
- add/upsert with endpoint set writes one audit entry per document, dry_run preserved
- detect() returns False (explicit opt-in only)
- End-to-end: get_collection returns an AgoraCollection wrapping a real ChromaCollection
"""

from unittest.mock import MagicMock


from mempalace import audit as audit_module
from mempalace.backend_agora import AgoraBackend, AgoraCollection
from mempalace.backends.base import (
    GetResult,
    HealthStatus,
    PalaceRef,
    QueryResult,
)
from mempalace.config_agora import AgoraConfig


# ── Helpers ─────────────────────────────────────────────────────────────


def _disabled_config():
    return AgoraConfig(endpoint=None)


def _enabled_config(dry_run=True):
    return AgoraConfig(endpoint="https://test.example/agora", dry_run=dry_run)


def _make_inner():
    """A MagicMock standing in for a ChromaCollection."""
    inner = MagicMock()
    inner.add.return_value = None
    inner.upsert.return_value = None
    inner.update.return_value = None
    inner.delete.return_value = None
    inner.count.return_value = 7
    inner.estimated_count.return_value = 7
    inner.health.return_value = HealthStatus.healthy()
    inner.query.return_value = QueryResult.empty()
    inner.get.return_value = GetResult.empty()
    return inner


# ── Disabled (no endpoint) — passthrough only ───────────────────────────


def test_disabled_add_does_not_write_audit(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _disabled_config())

    coll.add(documents=["hello"], ids=["fact-1"])

    inner.add.assert_called_once_with(
        documents=["hello"], ids=["fact-1"], metadatas=None, embeddings=None
    )
    assert not audit_path.exists()


def test_disabled_upsert_does_not_write_audit(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _disabled_config())

    coll.upsert(documents=["hello"], ids=["fact-2"])
    assert not audit_path.exists()
    inner.upsert.assert_called_once()


# ── Enabled — audit on add/upsert ───────────────────────────────────────


def test_enabled_add_writes_one_audit_per_id(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config(dry_run=True))

    coll.add(documents=["a", "b", "c"], ids=["id1", "id2", "id3"])

    entries = audit_module.read_audit_entries(audit_path)
    assert len(entries) == 3
    assert [e["id"] for e in entries] == ["id1", "id2", "id3"]
    assert all(e["op"] == "add" for e in entries)
    assert all(e["entry_type"] == "drawer_write" for e in entries)
    assert all(e["dry_run"] is True for e in entries)


def test_enabled_upsert_records_dry_run_false_when_configured(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config(dry_run=False))

    coll.upsert(documents=["x"], ids=["id-x"])
    entries = audit_module.read_audit_entries(audit_path)
    assert len(entries) == 1
    assert entries[0]["op"] == "upsert"
    assert entries[0]["entry_type"] == "drawer_write"
    assert entries[0]["dry_run"] is False


def test_enabled_does_not_attempt_network_call(tmp_path, monkeypatch):
    """Even with endpoint set, v0.1 must not touch the network on writes."""
    import socket

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("v0.1 must not make network calls")),
    )

    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config(dry_run=True))
    coll.add(documents=["x"], ids=["id-x"])  # must not raise


# ── Read passthrough ────────────────────────────────────────────────────


def test_query_is_pure_passthrough():
    inner = _make_inner()
    coll = AgoraCollection(inner, _disabled_config())
    coll.query(query_texts=["hello"], n_results=5)
    inner.query.assert_called_once_with(query_texts=["hello"], n_results=5)


def test_get_is_pure_passthrough():
    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config())
    coll.get(ids=["a", "b"])
    inner.get.assert_called_once_with(ids=["a", "b"])


def test_count_is_pure_passthrough():
    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config())
    assert coll.count() == 7


def test_delete_does_not_write_audit_even_when_enabled(tmp_path, monkeypatch):
    """delete is a removal, not a fact-emitting write — no audit entry."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config())
    coll.delete(ids=["x"])

    inner.delete.assert_called_once_with(ids=["x"], where=None)
    assert not audit_path.exists()


def test_update_does_not_write_audit_even_when_enabled(tmp_path, monkeypatch):
    """update is metadata-mutation; no classifier opportunity in v0.1."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)

    inner = _make_inner()
    coll = AgoraCollection(inner, _enabled_config())
    coll.update(ids=["x"], metadatas=[{"k": "v"}])
    assert not audit_path.exists()


# ── Backend-level surface ───────────────────────────────────────────────


def test_detect_returns_false():
    """Auto-detect must always return False — explicit opt-in only."""
    assert AgoraBackend.detect("/any/path") is False


def test_backend_name_and_spec_version():
    assert AgoraBackend.name == "agora"
    assert AgoraBackend.spec_version == "1.0"


def test_get_collection_wraps_inner_in_agora_collection(tmp_path, monkeypatch):
    """get_collection returns an AgoraCollection wrapping the inner ChromaCollection."""
    monkeypatch.delenv("MEMPALACE_AGORA_ENDPOINT", raising=False)

    backend = AgoraBackend()
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()

    coll = backend.get_collection(
        palace=PalaceRef(id="test-palace", local_path=str(palace_dir)),
        collection_name="mempalace_drawers",
        create=True,
    )
    assert isinstance(coll, AgoraCollection)


def test_close_delegates_to_inner():
    backend = AgoraBackend()
    backend._inner = MagicMock()
    backend.close()
    backend._inner.close.assert_called_once()


def test_health_delegates_to_inner():
    backend = AgoraBackend()
    backend._inner = MagicMock()
    backend._inner.health.return_value = HealthStatus.healthy("ok")
    result = backend.health()
    assert result.ok is True


# ── End-to-end via env-var selection ────────────────────────────────────


def test_end_to_end_passthrough_via_real_chroma(tmp_path, monkeypatch):
    """With MEMPALACE_BACKEND=agora and an endpoint, writes pass through to
    ChromaDB and produce audit entries.

    Uses real ChromaBackend underneath — no mocking of the storage layer."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_module, "_default_audit_path", lambda: audit_path)
    monkeypatch.setenv("MEMPALACE_AGORA_ENDPOINT", "https://test.example/agora")

    backend = AgoraBackend()
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()

    coll = backend.get_collection(
        palace=PalaceRef(id="e2e-palace", local_path=str(palace_dir)),
        collection_name="mempalace_drawers",
        create=True,
    )

    coll.add(
        documents=["the team adopted PostgreSQL"],
        ids=["fact-e2e-1"],
        metadatas=[{"wing": "decisions"}],
    )

    # Real ChromaCollection got the write
    assert coll.count() == 1
    got = coll.get(ids=["fact-e2e-1"])
    assert "the team adopted PostgreSQL" in got.documents

    # Audit log got the entry
    entries = audit_module.read_audit_entries(audit_path)
    assert len(entries) == 1
    assert entries[0]["id"] == "fact-e2e-1"
    assert entries[0]["op"] == "add"
