"""HTTP surface tests for the agora server.

Runs against the SQLite store through FastAPI's TestClient — no Docker, no
Postgres, no network. ``importorskip`` keeps the palace-side CI legs (which do
not install the server's dependency profile) green.
"""

import pytest

pytest.importorskip("fastapi", reason="agora server deps not installed")

from fastapi.testclient import TestClient  # noqa: E402

from agora.app import SCHEMA_VERSION_HEADER, create_app  # noqa: E402
from agora.auth import generate_key  # noqa: E402
from agora.config import AgoraServerConfig  # noqa: E402
from agora.storage.base import MigrationError  # noqa: E402
from agora.storage.sqlite import SQLiteStore  # noqa: E402
from contracts import SCHEMA_VERSION  # noqa: E402

DEPLOYMENT = "team-alpha"
OTHER_DEPLOYMENT = "team-beta"


@pytest.fixture
def store(tmp_path):
    store = SQLiteStore(path=str(tmp_path / "agora.sqlite3"))
    store.migrate()
    yield store
    store.close()


@pytest.fixture
def config():
    return AgoraServerConfig(store="sqlite", deployment_id=DEPLOYMENT, max_batch=5, max_limit=50)


@pytest.fixture
def client(config, store):
    return TestClient(create_app(config=config, store=store))


def issue(store, *, deployment=DEPLOYMENT, engineer="alice") -> str:
    key, record = generate_key(deployment_id=deployment, engineer_id=engineer)
    store.put_api_key(record=record)
    return key


@pytest.fixture
def key(store):
    return issue(store)


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def payload(*facts, schema_version=SCHEMA_VERSION):
    return {"facts": list(facts), "schema_version": schema_version}


def fact(subject="api", predicate="owned_by", obj="platform", **kwargs):
    return {"subject": subject, "predicate": predicate, "object": obj, **kwargs}


# ── Auth ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/facts", "/timeline"])
def test_reads_require_a_key(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized", "message": "valid API key required"}


def test_writes_require_a_key(client):
    assert client.post("/facts", json=payload(fact())).status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-key"},
        {"Authorization": "Bearer ak_deadbeef.wrongsecret"},
        {"Authorization": "Basic ak_deadbeef.secret"},
    ],
)
def test_malformed_credentials_are_rejected(client, header):
    assert client.get("/facts", headers=header).status_code == 401


def test_wrong_secret_for_a_real_key_is_rejected(client, store, key):
    key_id = key.split(".")[0]
    assert client.get("/facts", headers=auth(f"{key_id}.deadbeef")).status_code == 401


def test_revoked_key_is_rejected(client, store, key):
    store.revoke_api_key(key_id=key.split(".")[0])
    assert client.get("/facts", headers=auth(key)).status_code == 401


def test_every_auth_failure_looks_identical(client, store, key):
    # Distinguishing "no such key" from "wrong secret" would confirm which key
    # ids exist. Both must be byte-identical.
    unknown = client.get("/facts", headers=auth("ak_00000000.abc")).json()
    wrong = client.get("/facts", headers=auth(f"{key.split('.')[0]}.abc")).json()
    assert unknown == wrong


# ── Isolation ───────────────────────────────────────────────────────────


def test_a_key_only_sees_its_own_deployment(client, store):
    alpha_key = issue(store, deployment=DEPLOYMENT, engineer="alice")
    beta_key = issue(store, deployment=OTHER_DEPLOYMENT, engineer="bob")

    client.post("/facts", json=payload(fact("alpha-only")), headers=auth(alpha_key))
    client.post("/facts", json=payload(fact("beta-only")), headers=auth(beta_key))

    alpha = client.get("/facts", headers=auth(alpha_key)).json()["facts"]
    beta = client.get("/facts", headers=auth(beta_key)).json()["facts"]

    assert [f["subject"] for f in alpha] == ["alpha-only"]
    assert [f["subject"] for f in beta] == ["beta-only"]


def test_deployment_in_the_body_is_ignored(client, store, key):
    # extra="ignore" plus key-derived provenance: a client cannot write into
    # another deployment by decorating its payload.
    body = payload(fact(deployment_id=OTHER_DEPLOYMENT, engineer_id="mallory"))
    assert client.post("/facts", json=body, headers=auth(key)).status_code == 200

    stored = client.get("/facts", headers=auth(key)).json()["facts"][0]
    assert stored["engineer_id"] == "alice"


# ── POST /facts ─────────────────────────────────────────────────────────


def test_post_accepts_a_batch(client, key):
    response = client.post(
        "/facts",
        json=payload(fact("api"), fact("web", obj="frontend")),
        headers=auth(key),
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": 2, "rejected": 0, "message": None}


def test_post_is_partially_accepting(client, key):
    response = client.post(
        "/facts",
        json=payload(fact("good"), fact("dup"), fact("dup")),
        headers=auth(key),
    )
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (2, 1)
    assert "duplicate_open_triple: 1" in body["message"]


def test_post_rejects_an_oversized_batch(client, key):
    response = client.post(
        "/facts",
        json=payload(*[fact(f"s{i}") for i in range(6)]),  # max_batch=5
        headers=auth(key),
    )
    assert response.status_code == 413
    assert response.json()["error"] == "batch_too_large"


def test_post_rejects_a_newer_major_schema_version(client, key):
    response = client.post(
        "/facts", json=payload(fact(), schema_version="1.0.0"), headers=auth(key)
    )
    assert response.status_code == 400
    assert response.json()["error"] == "schema_version_unsupported"


def test_post_accepts_an_unknown_minor_version(client, key):
    # A client one release ahead must still be able to write.
    response = client.post(
        "/facts", json=payload(fact(), schema_version="0.9.3"), headers=auth(key)
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1


def test_per_fact_version_overrides_the_envelope(client, key):
    body = payload(fact(schema_version="0.2.0"), schema_version="0.1.0")
    client.post("/facts", json=body, headers=auth(key))
    stored = client.get("/facts", headers=auth(key)).json()["facts"][0]
    assert stored["schema_version"] == "0.2.0"


def test_post_rejects_a_malformed_body(client, key):
    response = client.post("/facts", json={"facts": [{"subject": "only"}]}, headers=auth(key))
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_post_ignores_unknown_fields(client, key):
    body = payload(fact(invented_field="from a newer client"))
    assert client.post("/facts", json=body, headers=auth(key)).status_code == 200


def test_post_empty_batch(client, key):
    response = client.post("/facts", json=payload(), headers=auth(key))
    assert response.json() == {"accepted": 0, "rejected": 0, "message": None}


def test_post_reports_validation_rejections(client, key):
    response = client.post("/facts", json=payload(fact(subject="   ")), headers=auth(key))
    body = response.json()
    assert (body["accepted"], body["rejected"]) == (0, 1)
    assert "empty_subject" in body["message"]


# ── GET /facts ──────────────────────────────────────────────────────────


@pytest.fixture
def seeded(client, key):
    client.post(
        "/facts",
        json=payload(
            fact("api", "owned_by", "platform", confidence=0.9),
            fact("api", "deprecates", "v1", valid_from="2026-01-01", valid_to="2026-06-01"),
            fact("web", "owned_by", "frontend", valid_from="2026-03-01", confidence=0.7),
        ),
        headers=auth(key),
    )
    return key


def test_get_returns_the_wire_shape_plus_server_fields(client, seeded):
    fact_out = client.get("/facts?subject=web", headers=auth(seeded)).json()["facts"][0]
    # The eight contract keys...
    for field in (
        "subject",
        "predicate",
        "object",
        "valid_from",
        "valid_to",
        "confidence",
        "source_session_id",
        "schema_version",
    ):
        assert field in fact_out
    # ...plus the additive server-side ones.
    assert fact_out["fact_id"].startswith("f_")
    assert fact_out["engineer_id"] == "alice"
    assert fact_out["current"] is True
    assert fact_out["recorded_at"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("subject=api", 2),
        ("predicate=owned_by", 2),
        ("object=v1", 1),
        ("current=true", 2),
        ("as_of=2026-04-01", 3),
        ("as_of=2026-07-01", 2),
        ("min_confidence=0.8", 2),
        ("subject=api&predicate=owned_by", 1),
    ],
)
def test_filters(client, seeded, query, expected):
    response = client.get(f"/facts?{query}", headers=auth(seeded))
    assert len(response.json()["facts"]) == expected


def test_limit_is_clamped_to_the_server_maximum(client, seeded):
    assert client.get("/facts?limit=100000", headers=auth(seeded)).status_code == 200


def test_limit_must_be_positive(client, seeded):
    assert client.get("/facts?limit=0", headers=auth(seeded)).status_code == 422


def test_min_confidence_is_range_checked(client, seeded):
    assert client.get("/facts?min_confidence=2", headers=auth(seeded)).status_code == 422


def test_pagination_walks_every_fact_once(client, key):
    client.post(
        "/facts",
        json=payload(*[fact(f"s{i:02d}") for i in range(5)]),
        headers=auth(key),
    )
    seen, cursor = [], None
    for _ in range(10):
        url = "/facts?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=auth(key)).json()
        seen.extend(f["subject"] for f in body["facts"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == [f"s{i:02d}" for i in range(5)]


def test_bad_cursor_is_a_400(client, seeded):
    response = client.get("/facts?cursor=%21%21%21", headers=auth(seeded))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_cursor"


# ── GET /timeline ───────────────────────────────────────────────────────


def test_timeline_is_ordered_by_validity(client, seeded):
    facts = client.get("/timeline", headers=auth(seeded)).json()["facts"]
    assert [f["valid_from"] for f in facts] == ["2026-01-01", "2026-03-01", None]


def test_timeline_subject_filter(client, seeded):
    facts = client.get("/timeline?subject=api", headers=auth(seeded)).json()["facts"]
    assert {f["subject"] for f in facts} == {"api"}


def test_timeline_bad_cursor_is_a_400(client, seeded):
    assert client.get("/timeline?cursor=%21%21", headers=auth(seeded)).status_code == 400


# ── GET /health ─────────────────────────────────────────────────────────


def test_health_without_a_key_reveals_nothing_about_the_deployment(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["schema_versions"] == ["0.x"]
    assert "deployment_id" not in body
    assert "fact_count" not in body


def test_health_with_a_key_is_an_operator_view(client, seeded):
    body = client.get("/health", headers=auth(seeded)).json()
    assert body["deployment_id"] == DEPLOYMENT
    assert body["store"] == "sqlite"
    assert body["store_ok"] is True
    assert body["fact_count"] == 3


def test_health_ignores_a_bad_key_rather_than_401ing(client):
    # Healthchecks must not fail because a credential rotated.
    assert client.get("/health", headers=auth("garbage")).status_code == 200


# ── Cross-cutting ───────────────────────────────────────────────────────


def test_every_response_carries_the_schema_version_header(client, key):
    for response in (
        client.get("/health"),
        client.get("/facts", headers=auth(key)),
        client.get("/facts"),
    ):
        assert response.headers[SCHEMA_VERSION_HEADER] == SCHEMA_VERSION


def test_unknown_route_uses_the_shared_error_shape(client):
    body = client.get("/nope").json()
    assert body == {"error": "not_found", "message": "Not Found"}


def test_server_entrypoint_binds_where_the_config_says(monkeypatch, tmp_path):
    """`agora-server` is thin, but it is the only thing the container runs."""
    import agora.main as main_module

    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls.update(kwargs)

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    monkeypatch.setenv("AGORA_STORE", "sqlite")
    monkeypatch.setenv("AGORA_SQLITE_PATH", str(tmp_path / "main.sqlite3"))
    monkeypatch.setenv("AGORA_AUTO_MIGRATE", "1")
    monkeypatch.setenv("AGORA_HOST", "127.0.0.1")
    monkeypatch.setenv("AGORA_PORT", "9999")

    main_module.main()

    assert (calls["host"], calls["port"]) == ("127.0.0.1", 9999)
    assert calls["app"].title == "MemAgora"


def test_refuses_to_start_against_an_unmigrated_schema(tmp_path):
    """Deploying new code before running migrations must fail at startup.

    The alternative is a server that comes up healthy and then fails every
    write deep inside a driver error — which is what happens if this guard is
    removed. The message has to say what to run.
    """
    fresh = SQLiteStore(path=str(tmp_path / "unmigrated.sqlite3"))
    with pytest.raises(MigrationError) as exc:
        create_app(config=AgoraServerConfig(store="sqlite"), store=fresh)
    assert "agora-admin migrate" in str(exc.value)
    # And it did not migrate behind the operator's back on the way out.
    assert fresh.migrate() != []
    fresh.close()


def test_a_partially_migrated_schema_is_also_refused(tmp_path):
    """The guard is about *pending* migrations, not just an empty database."""
    import agora.storage.sqlite as sqlite_module
    from agora.storage.base import load_migrations

    store = SQLiteStore(path=str(tmp_path / "old.sqlite3"))
    real_load = sqlite_module.load_migrations
    sqlite_module.load_migrations = lambda d: real_load(d)[:1]  # v0.3-era schema
    try:
        store.migrate()
    finally:
        sqlite_module.load_migrations = real_load

    assert store.pending_migrations() == [v for v, _ in load_migrations(store.migrations_dir)][1:]
    with pytest.raises(MigrationError):
        create_app(config=AgoraServerConfig(store="sqlite"), store=store)
    store.close()


def test_auto_migrate_when_enabled(tmp_path):
    fresh = SQLiteStore(path=str(tmp_path / "auto.sqlite3"))
    create_app(config=AgoraServerConfig(store="sqlite", auto_migrate=True), store=fresh)
    assert fresh.migrate() == []
    fresh.close()


# ── POST /ingest ────────────────────────────────────────────────────────


def decision(decision_id="d_1", **kwargs):
    body = {
        "decision_id": decision_id,
        "title": "Queue for the notifications service",
        "chosen": "SQS FIFO",
        "rationale": "Ordering is required per-recipient.",
    }
    body.update(kwargs)
    return body


def ingest_body(*, facts=(), decisions=(), closes=(), schema_version=SCHEMA_VERSION):
    return {
        "facts": list(facts),
        "decisions": list(decisions),
        "closes": list(closes),
        "schema_version": schema_version,
    }


def test_ingest_stores_a_decision_and_its_facts_together(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(
            decisions=[
                decision(
                    alternatives_rejected=["Kafka — too much operational surface"],
                    constraints=["Stay inside the current AWS account"],
                    open_questions=["DLQ before launch?"],
                    decided_on="2026-08-01",
                )
            ],
            facts=[fact("notifications", "uses", "SQS FIFO", decision_id="d_1")],
        ),
        headers=auth(key),
    )
    assert response.status_code == 200
    assert response.json() == {
        "facts_accepted": 1,
        "facts_rejected": 0,
        "decisions_accepted": 1,
        "decisions_rejected": 0,
        "facts_closed": 0,
        "message": None,
    }

    stored = client.get("/decisions/d_1", headers=auth(key)).json()
    assert stored["chosen"] == "SQS FIFO"
    assert stored["alternatives_rejected"] == ["Kafka — too much operational surface"]
    assert stored["open_questions"] == ["DLQ before launch?"]
    assert stored["engineer_id"] == "alice"

    linked = client.get("/facts?decision_id=d_1", headers=auth(key)).json()["facts"]
    assert [f["subject"] for f in linked] == ["notifications"]


def test_ingest_counts_the_two_halves_separately(client, key):
    client.post("/ingest", json=ingest_body(decisions=[decision()]), headers=auth(key))
    response = client.post(
        "/ingest",
        json=ingest_body(
            decisions=[decision()],  # duplicate id
            facts=[fact("new-thing")],
        ),
        headers=auth(key),
    )
    body = response.json()
    assert (body["facts_accepted"], body["facts_rejected"]) == (1, 0)
    assert (body["decisions_accepted"], body["decisions_rejected"]) == (0, 1)
    assert "duplicate_decision_id: 1" in body["message"]


def test_ingest_requires_a_key(client):
    assert client.post("/ingest", json=ingest_body(decisions=[decision()])).status_code == 401


def test_ingest_provenance_comes_from_the_key(client, store):
    beta_key = issue(store, deployment=OTHER_DEPLOYMENT, engineer="bob")
    client.post(
        "/ingest",
        json=ingest_body(decisions=[decision(engineer_id="mallory")]),
        headers=auth(beta_key),
    )
    stored = client.get("/decisions/d_1", headers=auth(beta_key)).json()
    assert stored["engineer_id"] == "bob"


def test_ingest_is_deployment_scoped(client, store, key):
    beta_key = issue(store, deployment=OTHER_DEPLOYMENT, engineer="bob")
    client.post("/ingest", json=ingest_body(decisions=[decision()]), headers=auth(beta_key))

    assert client.get("/decisions/d_1", headers=auth(key)).status_code == 404
    assert client.get("/decisions", headers=auth(key)).json()["decisions"] == []


def test_ingest_counts_the_whole_batch_against_the_limit(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(
            decisions=[decision(f"d_{i}") for i in range(3)],
            facts=[fact(f"s{i}") for i in range(3)],  # 6 items, max_batch=5
        ),
        headers=auth(key),
    )
    assert response.status_code == 413


def test_ingest_rejects_a_newer_major_schema_version(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(decisions=[decision()], schema_version="1.0.0"),
        headers=auth(key),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "schema_version_unsupported"


def test_ingest_rejects_an_over_long_rationale(client, key):
    """The prose cap is the privacy boundary made enforceable."""
    response = client.post(
        "/ingest",
        json=ingest_body(decisions=[decision(rationale="x" * 4001)]),
        headers=auth(key),
    )
    body = response.json()
    assert body["decisions_rejected"] == 1
    assert "rationale_too_long" in body["message"]


def test_ingest_ignores_unknown_decision_fields(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(decisions=[decision(invented_by_a_newer_client=True)]),
        headers=auth(key),
    )
    assert response.json()["decisions_accepted"] == 1


def test_ingest_empty_batch(client, key):
    response = client.post("/ingest", json=ingest_body(), headers=auth(key))
    assert response.json() == {
        "facts_accepted": 0,
        "facts_rejected": 0,
        "decisions_accepted": 0,
        "decisions_rejected": 0,
        "facts_closed": 0,
        "message": None,
    }


def test_post_facts_still_works_for_pre_decision_clients(client, key):
    """A v0.3 palace posts to /facts with no decisions and no decision_id."""
    response = client.post(
        "/facts", json=payload(fact(), schema_version="0.1.0"), headers=auth(key)
    )
    assert response.status_code == 200
    stored = client.get("/facts", headers=auth(key)).json()["facts"][0]
    assert stored["decision_id"] is None


# ── GET /decisions ──────────────────────────────────────────────────────


@pytest.fixture
def decided(client, key):
    client.post(
        "/ingest",
        json=ingest_body(decisions=[decision(f"d_{i}", title=f"Decision {i}") for i in range(3)]),
        headers=auth(key),
    )
    return key


def test_get_decisions_lists_them(client, decided):
    body = client.get("/decisions", headers=auth(decided)).json()
    assert {d["decision_id"] for d in body["decisions"]} == {"d_0", "d_1", "d_2"}


def test_get_decisions_filters_by_id_list(client, decided):
    body = client.get("/decisions?ids=d_0,d_2", headers=auth(decided)).json()
    assert {d["decision_id"] for d in body["decisions"]} == {"d_0", "d_2"}


def test_get_decisions_paginates(client, decided):
    seen, cursor = [], None
    for _ in range(5):
        url = "/decisions?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=auth(decided)).json()
        seen.extend(d["decision_id"] for d in body["decisions"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == ["d_0", "d_1", "d_2"]


def test_get_decisions_bad_cursor_is_a_400(client, decided):
    response = client.get("/decisions?cursor=%21%21", headers=auth(decided))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_cursor"


def test_get_one_decision_that_does_not_exist(client, decided):
    response = client.get("/decisions/d_nope", headers=auth(decided))
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_decisions_require_a_key(client, decided):
    assert client.get("/decisions").status_code == 401
    assert client.get("/decisions/d_0").status_code == 401


# ── Superseding through /ingest ─────────────────────────────────────────


def test_ingest_closes_the_old_fact_and_records_the_new_one(client, key):
    client.post(
        "/ingest",
        json=ingest_body(
            facts=[fact("notifications", "uses", "SQS FIFO", valid_from="2026-01-01")]
        ),
        headers=auth(key),
    )

    response = client.post(
        "/ingest",
        json=ingest_body(
            closes=[
                {
                    "subject": "notifications",
                    "predicate": "uses",
                    "object": "SQS FIFO",
                    "valid_to": "2026-09-01",
                }
            ],
            facts=[fact("notifications", "uses", "Kinesis", valid_from="2026-09-01")],
        ),
        headers=auth(key),
    )
    body = response.json()
    assert (body["facts_accepted"], body["facts_closed"]) == (1, 1)

    # Exactly one current answer, and the old one is still there, bounded.
    current = client.get("/facts?current=true", headers=auth(key)).json()["facts"]
    assert [f["object"] for f in current] == ["Kinesis"]
    historical = client.get("/facts?as_of=2026-05-01", headers=auth(key)).json()["facts"]
    assert [f["object"] for f in historical] == ["SQS FIFO"]


def test_closing_lets_the_replacement_reuse_the_same_object(client, key):
    """Without the close, re-opening a triple collides with the unique index."""
    payload_body = ingest_body(facts=[fact("api", "uses", "SQS")])
    client.post("/ingest", json=payload_body, headers=auth(key))

    blocked = client.post("/ingest", json=payload_body, headers=auth(key)).json()
    assert blocked["facts_rejected"] == 1

    allowed = client.post(
        "/ingest",
        json=ingest_body(
            closes=[{"subject": "api", "predicate": "uses", "object": "SQS"}],
            facts=[fact("api", "uses", "SQS", valid_from="2027-01-01")],
        ),
        headers=auth(key),
    ).json()
    assert (allowed["facts_closed"], allowed["facts_accepted"]) == (1, 1)


def test_a_close_that_matches_nothing_is_reported_not_fatal(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(
            closes=[{"subject": "ghost", "predicate": "uses", "object": "nothing"}],
            facts=[fact("real", "uses", "something")],
        ),
        headers=auth(key),
    )
    body = response.json()
    assert body["facts_closed"] == 0
    assert body["facts_accepted"] == 1  # the rest of the batch still lands
    assert body["facts_rejected"] == 0  # a missed close is not a rejected fact
    assert "close_matched_nothing: 1" in body["message"]


def test_one_deployment_cannot_close_anothers_fact(client, store, key):
    beta_key = issue(store, deployment=OTHER_DEPLOYMENT, engineer="bob")
    client.post("/ingest", json=ingest_body(facts=[fact("api", "uses", "SQS")]), headers=auth(key))

    response = client.post(
        "/ingest",
        json=ingest_body(closes=[{"subject": "api", "predicate": "uses", "object": "SQS"}]),
        headers=auth(beta_key),
    )
    assert response.json()["facts_closed"] == 0
    still_open = client.get("/facts?current=true", headers=auth(key)).json()["facts"]
    assert len(still_open) == 1


def test_closes_count_against_the_batch_limit(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(
            facts=[fact(f"s{i}") for i in range(3)],
            closes=[{"subject": f"s{i}", "predicate": "p", "object": "o"} for i in range(3)],
        ),
        headers=auth(key),
    )
    assert response.status_code == 413


def test_closes_require_a_full_triple(client, key):
    response = client.post(
        "/ingest",
        json=ingest_body(closes=[{"subject": "api", "predicate": "uses"}]),
        headers=auth(key),
    )
    assert response.status_code == 422
