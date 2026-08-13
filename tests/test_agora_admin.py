"""``agora-admin`` CLI tests.

Everything runs against a temporary SQLite store driven through the real
``main()`` entrypoint, so argument wiring and exit codes are covered rather
than just the handler functions.
"""

import json

import pytest

pytest.importorskip("fastapi", reason="agora server deps not installed")

from agora.admin import main  # noqa: E402
from agora.auth import parse_key  # noqa: E402
from agora.storage.sqlite import SQLiteStore  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Point the CLI at a throwaway SQLite deployment."""
    db = tmp_path / "agora.sqlite3"
    monkeypatch.setenv("AGORA_STORE", "sqlite")
    monkeypatch.setenv("AGORA_SQLITE_PATH", str(db))
    monkeypatch.setenv("AGORA_DEPLOYMENT_ID", "team-alpha")
    return db


def store_for(db) -> SQLiteStore:
    return SQLiteStore(path=str(db))


def test_no_command_prints_help(env, capsys):
    assert main([]) == 2
    assert "agora-admin" in capsys.readouterr().out


def test_migrate_is_idempotent(env, capsys):
    assert main(["migrate"]) == 0
    assert "applied migrations: 001" in capsys.readouterr().out
    assert main(["migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_issue_key_prints_the_secret_once_and_stores_only_the_hash(env, capsys):
    main(["migrate"])
    assert main(["issue-key", "--engineer", "alice"]) == 0
    out = capsys.readouterr().out

    key_line = out.strip().splitlines()[-1]
    key_id, secret = parse_key(key_line)

    store = store_for(env)
    record = store.get_api_key(key_id=key_id)
    store.close()

    assert record.engineer_id == "alice"
    assert record.deployment_id == "team-alpha"
    assert secret not in record.key_hash
    assert secret not in out.replace(key_line, "")  # printed exactly once


def test_issue_key_honors_a_deployment_override(env, capsys):
    main(["migrate"])
    main(["issue-key", "--engineer", "bob", "--deployment", "team-beta"])
    capsys.readouterr()

    assert main(["list-keys"]) == 0
    assert "no keys issued" in capsys.readouterr().out

    assert main(["list-keys", "--deployment", "team-beta"]) == 0
    assert "bob" in capsys.readouterr().out


def test_revoke_key(env, capsys):
    main(["migrate"])
    main(["issue-key", "--engineer", "alice"])
    key_id = parse_key(capsys.readouterr().out.strip().splitlines()[-1])[0]

    assert main(["revoke-key", key_id]) == 0
    assert "revoked" in capsys.readouterr().out

    # Revoking again is an error the operator should see.
    assert main(["revoke-key", key_id]) == 1
    assert "no active key" in capsys.readouterr().err


def test_revoke_unknown_key_is_an_error(env, capsys):
    main(["migrate"])
    assert main(["revoke-key", "ak_00000000"]) == 1


def test_list_keys_shows_state(env, capsys):
    main(["migrate"])
    main(["issue-key", "--engineer", "alice"])
    key_id = parse_key(capsys.readouterr().out.strip().splitlines()[-1])[0]
    main(["revoke-key", key_id])
    capsys.readouterr()

    main(["list-keys"])
    assert "revoked" in capsys.readouterr().out


def test_stats(env, capsys):
    main(["migrate"])
    main(["issue-key", "--engineer", "alice"])
    capsys.readouterr()

    assert main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "store      : sqlite (ok)" in out
    assert "facts      : 0" in out
    assert "keys       : 1" in out


# ── Export / import: the storage-swap path ──────────────────────────────


def seed(db, *, deployment="team-alpha"):
    from agora.storage.base import StoredFact, new_fact_id, utc_now_iso

    store = store_for(db)
    store.migrate()
    for engineer, subject in (("alice", "api"), ("bob", "web")):
        store.put_facts(
            deployment_id=deployment,
            engineer_id=engineer,
            facts=[
                StoredFact(
                    fact_id=new_fact_id(),
                    deployment_id=deployment,
                    engineer_id=engineer,
                    subject=subject,
                    predicate="owned_by",
                    object=f"{engineer}-team",
                    schema_version="0.1.0",
                    recorded_at=utc_now_iso(),
                    valid_from="2026-01-01",
                )
            ],
        )
    store.close()


def test_export_writes_one_json_object_per_fact(env, tmp_path, capsys):
    seed(env)
    out = tmp_path / "facts.jsonl"
    assert main(["export", "--output", str(out)]) == 0
    assert "exported 2 facts" in capsys.readouterr().out

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert {r["subject"] for r in rows} == {"api", "web"}
    assert {r["engineer_id"] for r in rows} == {"alice", "bob"}


def test_export_to_stdout(env, capsys):
    seed(env)
    assert main(["export"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(rows) == 2


def test_export_is_deployment_scoped(env, capsys):
    seed(env)
    assert main(["export", "--deployment", "team-beta"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_import_preserves_identity_and_provenance(env, tmp_path, monkeypatch, capsys):
    seed(env)
    dump = tmp_path / "facts.jsonl"
    main(["export", "--output", str(dump)])
    original = [json.loads(line) for line in dump.read_text().splitlines()]
    capsys.readouterr()

    # Swap the backend: a fresh, empty database standing in for "the team
    # moved off Postgres".
    target = tmp_path / "target.sqlite3"
    monkeypatch.setenv("AGORA_SQLITE_PATH", str(target))
    assert main(["migrate"]) == 0
    capsys.readouterr()
    assert main(["import", str(dump)]) == 0
    assert "imported 2 facts" in capsys.readouterr().out

    store = store_for(target)
    migrated = sorted(store.export_facts(deployment_id="team-alpha"), key=lambda f: f.subject)
    store.close()

    assert [f.subject for f in migrated] == ["api", "web"]
    for before, after in zip(sorted(original, key=lambda r: r["subject"]), migrated):
        assert after.fact_id == before["fact_id"]
        assert after.engineer_id == before["engineer_id"]
        assert after.recorded_at == before["recorded_at"]
        assert after.valid_from == before["valid_from"]


def test_import_skips_malformed_rows_and_keeps_going(env, tmp_path, capsys):
    main(["migrate"])
    dump = tmp_path / "mixed.jsonl"
    dump.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "fact_id": "f_1",
                        "deployment_id": "team-alpha",
                        "engineer_id": "alice",
                        "subject": "api",
                        "predicate": "owned_by",
                        "object": "platform",
                        "schema_version": "0.1.0",
                        "recorded_at": "2026-05-01T00:00:00+00:00",
                    }
                ),
                "{not json",
                "",
            ]
        )
    )
    assert main(["import", str(dump)]) == 0
    captured = capsys.readouterr()
    assert "imported 1 facts" in captured.out
    assert "skipping malformed row" in captured.err


def test_import_ignores_unknown_columns(env, tmp_path, capsys):
    """A dump from a newer server must still load into an older one."""
    main(["migrate"])
    dump = tmp_path / "future.jsonl"
    dump.write_text(
        json.dumps(
            {
                "fact_id": "f_1",
                "deployment_id": "team-alpha",
                "engineer_id": "alice",
                "subject": "api",
                "predicate": "owned_by",
                "object": "platform",
                "schema_version": "0.1.0",
                "recorded_at": "2026-05-01T00:00:00+00:00",
                "invented_by_a_later_version": True,
            }
        )
    )
    assert main(["import", str(dump)]) == 0
    assert "imported 1 facts" in capsys.readouterr().out


def test_import_reports_rejections(env, tmp_path, capsys):
    main(["migrate"])
    row = {
        "fact_id": "f_1",
        "deployment_id": "team-alpha",
        "engineer_id": "alice",
        "subject": "api",
        "predicate": "owned_by",
        "object": "platform",
        "schema_version": "0.1.0",
        "recorded_at": "2026-05-01T00:00:00+00:00",
    }
    dump = tmp_path / "dupes.jsonl"
    dump.write_text(json.dumps(row) + "\n" + json.dumps({**row, "fact_id": "f_2"}))

    assert main(["import", str(dump)]) == 0
    captured = capsys.readouterr()
    assert "imported 1 facts" in captured.out
    assert "duplicate_open_triple: 1" in captured.err


def test_missing_import_file_is_an_error(env, capsys):
    main(["migrate"])
    assert main(["import", "/nonexistent/facts.jsonl"]) == 1
    assert "error:" in capsys.readouterr().err


def test_postgres_without_a_dsn_fails_clearly(monkeypatch, capsys):
    monkeypatch.setenv("AGORA_STORE", "postgres")
    monkeypatch.delenv("AGORA_DSN", raising=False)
    assert main(["migrate"]) == 1
    assert "AGORA_DSN" in capsys.readouterr().err
