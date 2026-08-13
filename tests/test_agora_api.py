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
from agora.storage.sqlite import SQLiteStore  # noqa: E402

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


def payload(*facts, schema_version="0.1.0"):
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
        assert response.headers[SCHEMA_VERSION_HEADER] == "0.1.0"


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
    monkeypatch.setenv("AGORA_HOST", "127.0.0.1")
    monkeypatch.setenv("AGORA_PORT", "9999")

    main_module.main()

    assert (calls["host"], calls["port"]) == ("127.0.0.1", 9999)
    assert calls["app"].title == "MemAgora"


def test_auto_migrate_is_off_by_default(tmp_path):
    fresh = SQLiteStore(path=str(tmp_path / "unmigrated.sqlite3"))
    create_app(config=AgoraServerConfig(store="sqlite"), store=fresh)
    # No tables created: the app did not migrate behind the operator's back.
    assert fresh.migrate() == ["001"]
    fresh.close()


def test_auto_migrate_when_enabled(tmp_path):
    fresh = SQLiteStore(path=str(tmp_path / "auto.sqlite3"))
    create_app(config=AgoraServerConfig(store="sqlite", auto_migrate=True), store=fresh)
    assert fresh.migrate() == []
    fresh.close()
