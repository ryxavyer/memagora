"""Storage-layer tests for the agora server.

The bulk of the coverage is the shared conformance suite
(``agora.storage.testing.AbstractStoreContractSuite``) — the same suite a
third-party store implementation runs. It is exercised here against SQLite on
every CI leg, and against Postgres when ``AGORA_TEST_DSN`` is set (``-m
postgres``).

Nothing in this module needs FastAPI: the storage layer is stdlib-only apart
from psycopg, so these tests run everywhere the palace tests do.
"""

import os
from dataclasses import replace

import pytest

from agora.config import AgoraServerConfig, load_config
from agora.storage import (
    AgoraStore,
    AgoraStoreError,
    available_stores,
    build_store,
    get_store_class,
    register,
    unregister,
)
from agora.storage.base import (
    NULL_SORT_KEY,
    MigrationError,
    StoredFact,
    decode_cursor,
    encode_cursor,
    load_migrations,
    new_fact_id,
    normalize_fact,
    split_statements,
    utc_now_iso,
    validate_fact,
)
from agora.storage.sqlite import SQLiteStore
from agora.storage.testing import AbstractStoreContractSuite, make_fact


class TestSQLiteStore(AbstractStoreContractSuite):
    """The reference conformance run — always on, no services required."""

    @pytest.fixture
    def store(self, tmp_path):
        store = SQLiteStore(path=str(tmp_path / "agora.sqlite3"))
        store.migrate()
        yield store
        store.close()


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("AGORA_TEST_DSN"),
    reason="AGORA_TEST_DSN not set; see docs/deployment.md",
)
class TestPostgresStore(AbstractStoreContractSuite):
    """Same contract, reference backend. Requires a live Postgres."""

    @pytest.fixture
    def store(self):
        from agora.storage.postgres import PostgresStore

        store = PostgresStore(dsn=os.environ["AGORA_TEST_DSN"])
        store.migrate()
        # Clean at setup rather than teardown: a shared database has no
        # equivalent of SQLite's throwaway file, and some cases close the
        # store themselves so teardown cannot assume it is still usable.
        store.truncate_all()
        yield store
        store.close()


# ── Registry ────────────────────────────────────────────────────────────


def test_sqlite_is_registered():
    assert "sqlite" in available_stores()
    assert get_store_class("sqlite") is SQLiteStore


def test_unknown_store_raises_with_available_names():
    with pytest.raises(AgoraStoreError) as exc:
        get_store_class("cassandra")
    assert "sqlite" in str(exc.value)


def test_explicit_registration_wins_and_can_be_removed():
    class DummyStore(SQLiteStore):
        name = "dummy"

    register("dummy", DummyStore)
    try:
        assert get_store_class("dummy") is DummyStore
    finally:
        unregister("dummy")
    with pytest.raises(AgoraStoreError):
        get_store_class("dummy")


def test_build_store_from_config(tmp_path):
    config = AgoraServerConfig(store="sqlite", sqlite_path=str(tmp_path / "a.sqlite3"))
    store = build_store(config)
    try:
        assert isinstance(store, AgoraStore)
        assert store.migrate() == ["001"]
    finally:
        store.close()


# ── Config ──────────────────────────────────────────────────────────────


def test_config_defaults_are_conservative():
    config = load_config(env={})
    assert config.store == "postgres"
    assert config.deployment_id == "default"
    assert config.auto_migrate is False  # operators run migrations deliberately
    assert config.max_batch == 100


def test_config_reads_environment():
    config = load_config(
        env={
            "AGORA_STORE": "sqlite",
            "AGORA_SQLITE_PATH": "/tmp/x.sqlite3",
            "AGORA_DEPLOYMENT_ID": "team-alpha",
            "AGORA_AUTO_MIGRATE": "yes",
            "AGORA_MAX_BATCH": "7",
            "AGORA_PORT": "9000",
        }
    )
    assert config.store == "sqlite"
    assert config.sqlite_path == "/tmp/x.sqlite3"
    assert config.deployment_id == "team-alpha"
    assert config.auto_migrate is True
    assert config.max_batch == 7
    assert config.port == 9000


def test_config_ignores_unparseable_ints():
    assert load_config(env={"AGORA_MAX_BATCH": "lots"}).max_batch == 100


# ── Validation helpers ──────────────────────────────────────────────────


def _fact(**kwargs) -> StoredFact:
    return make_fact(
        kwargs.pop("subject", "s"),
        kwargs.pop("predicate", "p"),
        kwargs.pop("object", "o"),
        **kwargs,
    )


def test_validate_accepts_partial_iso_dates():
    assert validate_fact(_fact(valid_from="2026", valid_to="2026-06")) is None


def test_validate_rejects_unpadded_dates():
    # Lexicographic comparison is only meaningful for zero-padded values.
    assert validate_fact(_fact(valid_from="2026-1-1")) == "malformed_valid_from"


def test_validate_requires_provenance():
    assert validate_fact(replace(_fact(), engineer_id="")) == "missing_provenance"


def test_normalize_lowercases_predicate_and_trims():
    fact = normalize_fact(_fact(subject=" A ", predicate="Reports To", object=" B "))
    assert (fact.subject, fact.predicate, fact.object) == ("A", "reports_to", "B")


def test_fact_ids_are_unique():
    assert new_fact_id() != new_fact_id()


def test_recorded_at_is_utc_iso():
    stamp = utc_now_iso()
    assert stamp.endswith("+00:00")


# ── Cursors ─────────────────────────────────────────────────────────────


def test_cursor_round_trip():
    assert decode_cursor(encode_cursor("2026-01-01", "f_abc"), expected=2) == [
        "2026-01-01",
        "f_abc",
    ]


def test_cursor_is_url_safe():
    cursor = encode_cursor(NULL_SORT_KEY, "f_" + "z" * 32)
    assert "=" not in cursor and "/" not in cursor and "+" not in cursor


def test_cursor_arity_is_checked():
    with pytest.raises(ValueError):
        decode_cursor(encode_cursor("only-one"), expected=2)


def test_cursor_rejects_garbage():
    with pytest.raises(ValueError):
        decode_cursor("!!!not base64!!!", expected=2)


# ── Migration runner ────────────────────────────────────────────────────


def test_split_statements_ignores_semicolons_in_comments():
    sql = "-- a comment; with a semicolon\nCREATE TABLE t (a TEXT);\nCREATE INDEX i ON t(a);"
    assert split_statements(sql) == ["CREATE TABLE t (a TEXT)", "CREATE INDEX i ON t(a)"]


def test_load_migrations_is_ordered_and_versioned():
    from pathlib import Path

    migrations = load_migrations(Path("agora/storage/migrations/sqlite"))
    assert [version for version, _ in migrations] == sorted(v for v, _ in migrations)
    assert migrations[0][0] == "001"


def test_load_migrations_rejects_a_missing_directory(tmp_path):
    with pytest.raises(MigrationError):
        load_migrations(tmp_path / "nope")


def test_load_migrations_rejects_an_empty_directory(tmp_path):
    with pytest.raises(MigrationError):
        load_migrations(tmp_path)


def test_migrate_reports_versions_applied(tmp_path):
    store = SQLiteStore(path=str(tmp_path / "fresh.sqlite3"))
    try:
        assert store.migrate() == ["001"]
        assert store.migrate() == []
    finally:
        store.close()


def test_store_does_no_io_before_first_use(tmp_path):
    path = tmp_path / "lazy.sqlite3"
    SQLiteStore(path=str(path))
    assert not path.exists()
