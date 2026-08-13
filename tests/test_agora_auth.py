"""API key minting, parsing, verification, and version negotiation."""

import pytest

pytest.importorskip("fastapi", reason="agora server deps not installed")

from agora.auth import (  # noqa: E402
    KEY_ID_PREFIX,
    generate_key,
    hash_secret,
    parse_key,
    verify_secret,
)
from agora.versioning import (  # noqa: E402
    SERVER_SCHEMA_VERSION,
    UnsupportedSchemaVersion,
    check_supported,
    parse_version,
    resolve_fact_version,
    supported_versions,
)


# ── Key minting ─────────────────────────────────────────────────────────


def test_generated_key_parses_back_to_its_record():
    key, record = generate_key(deployment_id="team", engineer_id="alice")
    key_id, secret = parse_key(key)
    assert key_id == record.key_id
    assert verify_secret(record, secret)


def test_key_id_is_public_and_the_secret_is_not_stored():
    key, record = generate_key(deployment_id="team", engineer_id="alice")
    _, secret = parse_key(key)
    assert record.key_id.startswith(KEY_ID_PREFIX)
    assert secret not in record.key_hash
    assert record.key_hash == hash_secret(secret)


def test_keys_are_unique():
    first, _ = generate_key(deployment_id="team", engineer_id="alice")
    second, _ = generate_key(deployment_id="team", engineer_id="alice")
    assert first != second


def test_secret_has_128_bits_of_entropy():
    key, _ = generate_key(deployment_id="team", engineer_id="alice")
    _, secret = parse_key(key)
    assert len(secret) == 32  # hex chars
    int(secret, 16)  # and is hex


def test_new_keys_are_active():
    _, record = generate_key(deployment_id="team", engineer_id="alice")
    assert record.active is True
    assert record.deployment_id == "team"
    assert record.engineer_id == "alice"


# ── Parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-dot",
        "ak_abc",  # no secret
        "ak_abc.",  # empty secret
        "bk_abc.secret",  # wrong prefix
        ".secret",
    ],
)
def test_malformed_keys_do_not_parse(raw):
    assert parse_key(raw) is None


def test_secret_may_contain_dots_without_breaking_the_split():
    # partition() splits on the first dot only, so a secret is never truncated.
    assert parse_key("ak_abc.def.ghi") == ("ak_abc", "def.ghi")


def test_verify_rejects_a_wrong_secret():
    _, record = generate_key(deployment_id="team", engineer_id="alice")
    assert verify_secret(record, "not-the-secret") is False


# ── Schema versions ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version,expected",
    [("1.2.3", (1, 2, 3)), ("0.1", (0, 1, 0)), ("2", (2, 0, 0))],
)
def test_parse_version(version, expected):
    assert parse_version(version) == expected


@pytest.mark.parametrize("version", ["", "   ", "x.y.z", "0.1.beta", None])
def test_parse_version_rejects_junk(version):
    with pytest.raises(UnsupportedSchemaVersion):
        parse_version(version)


def test_same_major_is_supported_regardless_of_minor():
    check_supported("0.1.0")
    check_supported("0.99.7")


def test_newer_major_is_refused():
    with pytest.raises(UnsupportedSchemaVersion) as exc:
        check_supported("1.0.0")
    assert "upgrade the agora deployment" in str(exc.value)


def test_older_major_without_a_migration_is_refused(monkeypatch):
    monkeypatch.setattr("agora.versioning.SERVER_SCHEMA_VERSION", "2.0.0")
    with pytest.raises(UnsupportedSchemaVersion):
        check_supported("1.0.0")


def test_older_major_with_a_registered_migration_is_accepted(monkeypatch):
    monkeypatch.setattr("agora.versioning.SERVER_SCHEMA_VERSION", "2.0.0")
    monkeypatch.setitem(__import__("agora.versioning", fromlist=["MIGRATIONS"]).MIGRATIONS, 1, dict)
    check_supported("1.4.0")


def test_per_fact_version_wins_over_the_envelope():
    assert resolve_fact_version("0.2.0", "0.1.0") == "0.2.0"
    assert resolve_fact_version(None, "0.1.0") == "0.1.0"


def test_supported_versions_are_advertised_by_major():
    assert supported_versions() == ["0.x"]
    assert SERVER_SCHEMA_VERSION.startswith("0.")
