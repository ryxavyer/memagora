"""Shared conformance suite for :class:`AgoraStore` implementations.

Mirrors RFC 001 §7's ``AbstractBackendContractSuite`` pattern: every store —
in-tree or third-party — proves it honors the contract by subclassing this and
supplying a ``store`` fixture::

    class TestMyStore(AbstractStoreContractSuite):
        @pytest.fixture
        def store(self):
            s = MyStore(dsn=os.environ["MY_DSN"])
            s.migrate()
            yield s
            s.close()

The suite is the definition of "swappable". A team replacing Postgres runs
this against their implementation before pointing engineers at it.

Lives in the shipped package rather than in ``tests/`` so third-party store
authors get it from ``pip install memagora-agora``.
"""

import pytest

from .base import (
    ApiKeyRecord,
    FactQuery,
    StoreClosedError,
    StoredFact,
    new_fact_id,
    utc_now_iso,
)

DEPLOYMENT = "team-alpha"
OTHER_DEPLOYMENT = "team-beta"
ENGINEER = "alice"


def make_fact(subject, predicate, obj, **kwargs) -> StoredFact:
    """Build a fact with server-side fields filled in.

    ``deployment_id`` / ``engineer_id`` are set to obviously-wrong values on
    purpose: ``put_facts`` must overwrite them from its own arguments, never
    trust what the caller supplied.
    """
    fields = {
        "fact_id": new_fact_id(),
        "deployment_id": "client-supplied-junk",
        "engineer_id": "client-supplied-junk",
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "schema_version": "0.1.0",
        "recorded_at": utc_now_iso(),
    }
    fields.update(kwargs)
    return StoredFact(**fields)


class AbstractStoreContractSuite:
    """Contract tests every store must pass. Subclass with a ``store`` fixture."""

    # ── Schema ──────────────────────────────────────────────────────────

    def test_migrate_is_idempotent(self, store):
        assert store.migrate() == []  # fixture already migrated
        assert store.migrate() == []

    # ── Write / read round trip ─────────────────────────────────────────

    def test_round_trip(self, store):
        result = store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact("auth-service", "owned_by", "platform-team")],
        )
        assert (result.accepted, result.rejected) == (1, 0)

        page = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery())
        assert len(page.facts) == 1
        fact = page.facts[0]
        assert (fact.subject, fact.predicate, fact.object) == (
            "auth-service",
            "owned_by",
            "platform-team",
        )
        assert fact.current is True

    def test_provenance_comes_from_arguments_not_payload(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact("a", "b", "c")],
        )
        fact = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery()).facts[0]
        assert fact.deployment_id == DEPLOYMENT
        assert fact.engineer_id == ENGINEER

    def test_predicate_is_normalized(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact("  api  ", "Owned By", "  team  ")],
        )
        fact = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery()).facts[0]
        assert (fact.subject, fact.predicate, fact.object) == ("api", "owned_by", "team")

    def test_unicode_survives_round_trip(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact("café-service", "维护者", "команда-π")],
        )
        fact = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery()).facts[0]
        assert (fact.subject, fact.object) == ("café-service", "команда-π")

    # ── Isolation ───────────────────────────────────────────────────────

    def test_deployments_cannot_see_each_other(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact("secret", "known_to", "alpha")],
        )
        store.put_facts(
            deployment_id=OTHER_DEPLOYMENT,
            engineer_id="bob",
            facts=[make_fact("other-secret", "known_to", "beta")],
        )

        alpha = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery()).facts
        beta = store.get_facts(deployment_id=OTHER_DEPLOYMENT, query=FactQuery()).facts

        assert [f.subject for f in alpha] == ["secret"]
        assert [f.subject for f in beta] == ["other-secret"]
        assert store.count_facts(deployment_id=DEPLOYMENT) == 1
        assert list(store.export_facts(deployment_id=OTHER_DEPLOYMENT))[0].subject == "other-secret"

    def test_same_triple_in_two_deployments_is_not_a_duplicate(self, store):
        fact = ("shared", "owned_by", "someone")
        first = store.put_facts(
            deployment_id=DEPLOYMENT, engineer_id=ENGINEER, facts=[make_fact(*fact)]
        )
        second = store.put_facts(
            deployment_id=OTHER_DEPLOYMENT, engineer_id="bob", facts=[make_fact(*fact)]
        )
        assert first.accepted == 1 and second.accepted == 1

    # ── Dedup ───────────────────────────────────────────────────────────

    def test_duplicate_open_triple_is_rejected(self, store):
        facts = [make_fact("api", "owned_by", "team"), make_fact("api", "owned_by", "team")]
        result = store.put_facts(deployment_id=DEPLOYMENT, engineer_id=ENGINEER, facts=facts)
        assert result.accepted == 1
        assert result.rejected == 1
        assert result.reasons == {"duplicate_open_triple": 1}

    def test_closed_interval_does_not_block_a_second_row(self, store):
        result = store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact(
                    "api", "owned_by", "team", valid_from="2024-01-01", valid_to="2025-01-01"
                ),
                make_fact("api", "owned_by", "team", valid_from="2025-01-02"),
            ],
        )
        assert (result.accepted, result.rejected) == (2, 0)

    # ── Validation ──────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "kwargs,reason",
        [
            ({"subject": ""}, "empty_subject"),
            ({"predicate": "   "}, "empty_predicate"),
            ({"object": ""}, "empty_object"),
            ({"subject": "x" * 513}, "subject_too_long"),
            ({"valid_from": "yesterday"}, "malformed_valid_from"),
            ({"valid_to": "20260101"}, "malformed_valid_to"),
            ({"valid_from": "2026-02-01", "valid_to": "2026-01-01"}, "inverted_validity"),
            ({"confidence": 1.5}, "confidence_out_of_range"),
            ({"confidence": -0.1}, "confidence_out_of_range"),
        ],
    )
    def test_invalid_facts_are_rejected_with_a_reason(self, store, kwargs, reason):
        # Copy first: the parametrize dicts are built once at class-definition
        # time and shared by every subclass of this suite, so popping from the
        # originals would empty them before the next store's run.
        kwargs = dict(kwargs)
        subject = kwargs.pop("subject", "s")
        predicate = kwargs.pop("predicate", "p")
        obj = kwargs.pop("object", "o")
        result = store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact(subject, predicate, obj, **kwargs)],
        )
        assert result.accepted == 0
        assert result.reasons == {reason: 1}

    def test_partial_acceptance(self, store):
        result = store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact("good", "p", "o"),
                make_fact("", "p", "o"),
                make_fact("also-good", "p", "o"),
            ],
        )
        assert (result.accepted, result.rejected) == (2, 1)
        assert store.count_facts(deployment_id=DEPLOYMENT) == 2

    def test_empty_batch(self, store):
        result = store.put_facts(deployment_id=DEPLOYMENT, engineer_id=ENGINEER, facts=[])
        assert (result.accepted, result.rejected, result.reasons) == (0, 0, {})

    # ── Filters ─────────────────────────────────────────────────────────

    def _seed_filter_corpus(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact("api", "owned_by", "platform", confidence=0.9),
                make_fact(
                    "api", "deprecates", "v1", valid_from="2026-01-01", valid_to="2026-06-01"
                ),
                make_fact("web", "owned_by", "frontend", valid_from="2026-03-01", confidence=0.7),
            ],
        )

    def test_triple_filters(self, store):
        self._seed_filter_corpus(store)
        q = FactQuery(subject="api")
        assert len(store.get_facts(deployment_id=DEPLOYMENT, query=q).facts) == 2
        q = FactQuery(predicate="owned_by")
        assert len(store.get_facts(deployment_id=DEPLOYMENT, query=q).facts) == 2
        q = FactQuery(object="v1")
        assert len(store.get_facts(deployment_id=DEPLOYMENT, query=q).facts) == 1
        q = FactQuery(subject="api", predicate="owned_by")
        assert len(store.get_facts(deployment_id=DEPLOYMENT, query=q).facts) == 1

    def test_as_of_is_inclusive_and_treats_null_as_unbounded(self, store):
        self._seed_filter_corpus(store)

        during = store.get_facts(
            deployment_id=DEPLOYMENT, query=FactQuery(as_of="2026-04-01")
        ).facts
        assert {f.object for f in during} == {"platform", "v1", "frontend"}

        after = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery(as_of="2026-07-01")).facts
        assert {f.object for f in after} == {"platform", "frontend"}

        before = store.get_facts(
            deployment_id=DEPLOYMENT, query=FactQuery(as_of="2025-12-01")
        ).facts
        assert {f.object for f in before} == {"platform"}

        edge = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery(as_of="2026-06-01")).facts
        assert "v1" in {f.object for f in edge}

    def test_current_only(self, store):
        self._seed_filter_corpus(store)
        facts = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery(current_only=True)).facts
        assert {f.object for f in facts} == {"platform", "frontend"}

    def test_min_confidence(self, store):
        self._seed_filter_corpus(store)
        q = FactQuery(min_confidence=0.8)
        facts = store.get_facts(deployment_id=DEPLOYMENT, query=q).facts
        assert {f.object for f in facts} == {"platform", "v1"}

    # ── Pagination ──────────────────────────────────────────────────────

    def test_pagination_covers_every_row_exactly_once(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact(f"s{i:02d}", "p", "o") for i in range(25)],
        )

        seen, cursor, pages = [], None, 0
        while True:
            page = store.get_facts(
                deployment_id=DEPLOYMENT, query=FactQuery(limit=10, cursor=cursor)
            )
            seen.extend(f.subject for f in page.facts)
            pages += 1
            cursor = page.next_cursor
            if cursor is None:
                break
            assert pages < 10, "pagination did not terminate"

        assert len(seen) == 25
        assert len(set(seen)) == 25

    def test_last_page_has_no_cursor(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[make_fact(f"s{i}", "p", "o") for i in range(3)],
        )
        page = store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery(limit=10))
        assert page.next_cursor is None

    def test_malformed_cursor_raises(self, store):
        with pytest.raises(ValueError):
            store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery(cursor="not-a-cursor!!"))

    # ── Timeline ────────────────────────────────────────────────────────

    def test_timeline_orders_by_valid_from_with_unbounded_last(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact("c", "p", "o", valid_from="2026-05-01"),
                make_fact("a", "p", "o", valid_from="2024-01-01"),
                make_fact("d", "p", "o"),
                make_fact("b", "p", "o", valid_from="2025-06-01"),
            ],
        )
        page = store.timeline(deployment_id=DEPLOYMENT)
        assert [f.subject for f in page.facts] == ["a", "b", "c", "d"]

    def test_timeline_subject_filter_matches_either_end(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact("api", "owned_by", "platform", valid_from="2026-01-01"),
                make_fact("web", "depends_on", "api", valid_from="2026-02-01"),
                make_fact("unrelated", "p", "o", valid_from="2026-03-01"),
            ],
        )
        page = store.timeline(deployment_id=DEPLOYMENT, subject="api")
        assert [f.subject for f in page.facts] == ["api", "web"]

    def test_timeline_is_deployment_scoped(self, store):
        store.put_facts(
            deployment_id=OTHER_DEPLOYMENT,
            engineer_id="bob",
            facts=[make_fact("beta-only", "p", "o")],
        )
        assert store.timeline(deployment_id=DEPLOYMENT).facts == []

    def test_timeline_paginates(self, store):
        store.put_facts(
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            facts=[
                make_fact(f"s{i:02d}", "p", "o", valid_from=f"2026-01-{i + 1:02d}")
                for i in range(12)
            ],
        )
        seen, cursor = [], None
        for _ in range(10):
            page = store.timeline(deployment_id=DEPLOYMENT, limit=5, cursor=cursor)
            seen.extend(f.subject for f in page.facts)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(seen) == 12 and len(set(seen)) == 12

    # ── API keys ────────────────────────────────────────────────────────

    def test_api_key_lifecycle(self, store):
        record = ApiKeyRecord(
            key_id="ak_abc123",
            key_hash="hash-value",
            deployment_id=DEPLOYMENT,
            engineer_id=ENGINEER,
            created_at=utc_now_iso(),
        )
        store.put_api_key(record=record)

        fetched = store.get_api_key(key_id="ak_abc123")
        assert fetched is not None
        assert fetched.engineer_id == ENGINEER
        assert fetched.deployment_id == DEPLOYMENT
        assert fetched.active is True

        assert store.revoke_api_key(key_id="ak_abc123") is True
        assert store.get_api_key(key_id="ak_abc123").active is False
        # Revoking twice is not an error but reports that nothing changed.
        assert store.revoke_api_key(key_id="ak_abc123") is False

    def test_unknown_api_key_is_none(self, store):
        assert store.get_api_key(key_id="ak_missing") is None
        assert store.revoke_api_key(key_id="ak_missing") is False

    def test_list_api_keys_is_deployment_scoped(self, store):
        for key_id, deployment in (
            ("ak_1", DEPLOYMENT),
            ("ak_2", DEPLOYMENT),
            ("ak_3", OTHER_DEPLOYMENT),
        ):
            store.put_api_key(
                record=ApiKeyRecord(
                    key_id=key_id,
                    key_hash=f"hash-{key_id}",
                    deployment_id=deployment,
                    engineer_id=ENGINEER,
                    created_at=utc_now_iso(),
                )
            )
        assert {k.key_id for k in store.list_api_keys(deployment_id=DEPLOYMENT)} == {"ak_1", "ak_2"}

    # ── Lifecycle ───────────────────────────────────────────────────────

    def test_health_reports_backend(self, store):
        health = store.health()
        assert health.ok is True
        assert health.backend == store.name

    def test_use_after_close_raises(self, store):
        store.close()
        with pytest.raises(StoreClosedError):
            store.get_facts(deployment_id=DEPLOYMENT, query=FactQuery())
        assert store.health().ok is False

    def test_close_is_idempotent(self, store):
        store.close()
        store.close()
