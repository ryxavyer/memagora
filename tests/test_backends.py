import sqlite3

import chromadb
import pytest

from mempalace.backends import (
    GetResult,
    PalaceRef,
    QueryResult,
    UnsupportedFilterError,
    available_backends,
    get_backend,
)
from mempalace.backends.chroma import ChromaBackend, ChromaCollection, _fix_blob_seq_ids


class _FakeCollection:
    """Stand-in for a chromadb.Collection returning raw chroma-shaped dicts."""

    def __init__(self, query_response=None, get_response=None, count_value=7):
        self.calls = []
        self._query_response = query_response or {
            "ids": [["a", "b"]],
            "documents": [["da", "db"]],
            "metadatas": [[{"wing": "w1"}, {"wing": "w2"}]],
            "distances": [[0.1, 0.2]],
        }
        self._get_response = get_response or {
            "ids": ["a"],
            "documents": ["da"],
            "metadatas": [{"wing": "w1"}],
        }
        self._count_value = count_value

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._query_response

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self._get_response

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def count(self):
        self.calls.append(("count", {}))
        return self._count_value


def test_chroma_collection_returns_typed_query_result():
    fake = _FakeCollection()
    collection = ChromaCollection(fake)

    result = collection.query(query_texts=["q"])

    assert isinstance(result, QueryResult)
    assert result.ids == [["a", "b"]]
    assert result.documents == [["da", "db"]]
    assert result.metadatas == [[{"wing": "w1"}, {"wing": "w2"}]]
    assert result.distances == [[0.1, 0.2]]
    assert result.embeddings is None


def test_chroma_collection_returns_typed_get_result():
    fake = _FakeCollection()
    collection = ChromaCollection(fake)

    result = collection.get(where={"wing": "w1"})

    assert isinstance(result, GetResult)
    assert result.ids == ["a"]
    assert result.documents == ["da"]
    assert result.metadatas == [{"wing": "w1"}]


def test_query_result_empty_preserves_outer_dimension():
    empty = QueryResult.empty(num_queries=2)
    assert empty.ids == [[], []]
    assert empty.documents == [[], []]
    assert empty.distances == [[], []]
    assert empty.embeddings is None


def test_typed_results_support_dict_compat_access():
    """Transitional compat shim per base.py — retained until callers migrate to attrs."""
    result = GetResult(ids=["a"], documents=["da"], metadatas=[{"w": 1}])
    assert result["ids"] == ["a"]
    assert result.get("documents") == ["da"]
    assert result.get("missing", "default") == "default"
    assert "ids" in result
    assert "missing" not in result


def test_chroma_collection_query_empty_result_preserves_outer_shape():
    fake = _FakeCollection(
        query_response={"ids": [], "documents": [], "metadatas": [], "distances": []}
    )
    collection = ChromaCollection(fake)

    result = collection.query(query_texts=["q1", "q2"])
    assert result.ids == [[], []]
    assert result.documents == [[], []]
    assert result.distances == [[], []]


def test_chroma_collection_rejects_unknown_where_operator():
    fake = _FakeCollection()
    collection = ChromaCollection(fake)

    with pytest.raises(UnsupportedFilterError):
        collection.query(query_texts=["q"], where={"$regex": "foo"})


def test_chroma_collection_delegates_writes():
    fake = _FakeCollection()
    collection = ChromaCollection(fake)

    collection.add(documents=["d"], ids=["1"], metadatas=[{"wing": "w"}])
    collection.upsert(documents=["u"], ids=["2"], metadatas=[{"room": "r"}])
    collection.delete(ids=["1"])
    assert collection.count() == 7

    kinds = [call[0] for call in fake.calls]
    assert kinds == ["add", "upsert", "delete", "count"]


def test_registry_exposes_chroma_by_default():
    names = available_backends()
    assert "chroma" in names
    assert isinstance(get_backend("chroma"), ChromaBackend)


def test_registry_unknown_backend_raises():
    with pytest.raises(KeyError):
        get_backend("no-such-backend-exists")


def test_resolve_backend_priority_order(tmp_path):
    from mempalace.backends import resolve_backend_for_palace

    # explicit kwarg wins over everything
    assert resolve_backend_for_palace(explicit="pg", config_value="lance") == "pg"
    # config value wins over env / default
    assert resolve_backend_for_palace(config_value="lance", env_value="qdrant") == "lance"
    # env wins over default
    assert resolve_backend_for_palace(env_value="qdrant", default="chroma") == "qdrant"
    # falls back to default
    assert resolve_backend_for_palace() == "chroma"


def test_chroma_detect_matches_palace_with_chroma_sqlite(tmp_path):
    (tmp_path / "chroma.sqlite3").write_bytes(b"")
    assert ChromaBackend.detect(str(tmp_path)) is True
    assert ChromaBackend.detect(str(tmp_path.parent)) is False


def test_chroma_backend_accepts_palace_ref_kwarg(tmp_path):
    palace_path = tmp_path / "palace"
    backend = ChromaBackend()
    collection = backend.get_collection(
        palace=PalaceRef(id=str(palace_path), local_path=str(palace_path)),
        collection_name="mempalace_drawers",
        create=True,
    )
    assert palace_path.is_dir()
    assert isinstance(collection, ChromaCollection)


def test_chroma_backend_create_false_raises_without_creating_directory(tmp_path):
    palace_path = tmp_path / "missing-palace"

    with pytest.raises(FileNotFoundError):
        ChromaBackend().get_collection(
            str(palace_path),
            collection_name="mempalace_drawers",
            create=False,
        )

    assert not palace_path.exists()


def test_chroma_backend_create_true_creates_directory_and_collection(tmp_path):
    palace_path = tmp_path / "palace"

    collection = ChromaBackend().get_collection(
        str(palace_path),
        collection_name="mempalace_drawers",
        create=True,
    )

    assert palace_path.is_dir()
    assert isinstance(collection, ChromaCollection)

    client = chromadb.PersistentClient(path=str(palace_path))
    client.get_collection("mempalace_drawers")


def test_chroma_backend_creates_collection_with_cosine_distance(tmp_path):
    palace_path = tmp_path / "palace"

    ChromaBackend().get_collection(
        str(palace_path),
        collection_name="mempalace_drawers",
        create=True,
    )

    client = chromadb.PersistentClient(path=str(palace_path))
    col = client.get_collection("mempalace_drawers")
    assert col.metadata.get("hnsw:space") == "cosine"


def test_fix_blob_seq_ids_converts_blobs_to_integers(tmp_path):
    """Simulate a ChromaDB 0.6.x database with BLOB seq_ids and verify repair."""
    db_path = tmp_path / "chroma.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE embeddings (rowid INTEGER PRIMARY KEY, seq_id)")
    conn.execute("CREATE TABLE max_seq_id (rowid INTEGER PRIMARY KEY, seq_id)")
    # Insert BLOB seq_ids like ChromaDB 0.6.x would
    blob_42 = (42).to_bytes(8, byteorder="big")
    blob_99 = (99).to_bytes(8, byteorder="big")
    conn.execute("INSERT INTO embeddings (seq_id) VALUES (?)", (blob_42,))
    conn.execute("INSERT INTO max_seq_id (seq_id) VALUES (?)", (blob_99,))
    conn.commit()
    conn.close()

    _fix_blob_seq_ids(str(tmp_path))

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT seq_id, typeof(seq_id) FROM embeddings").fetchone()
    assert row == (42, "integer")
    row = conn.execute("SELECT seq_id, typeof(seq_id) FROM max_seq_id").fetchone()
    assert row == (99, "integer")
    conn.close()


def test_fix_blob_seq_ids_noop_without_blobs(tmp_path):
    """No error when seq_ids are already integers."""
    db_path = tmp_path / "chroma.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE embeddings (rowid INTEGER PRIMARY KEY, seq_id INTEGER)")
    conn.execute("INSERT INTO embeddings (seq_id) VALUES (42)")
    conn.commit()
    conn.close()

    _fix_blob_seq_ids(str(tmp_path))

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT seq_id, typeof(seq_id) FROM embeddings").fetchone()
    assert row == (42, "integer")
    conn.close()


def test_fix_blob_seq_ids_noop_without_database(tmp_path):
    """No error when palace has no chroma.sqlite3."""
    _fix_blob_seq_ids(str(tmp_path))  # should not raise
