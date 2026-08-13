-- MemAgora fact store — SQLite variant.
--
-- Same shape as the Postgres reference (migrations/postgres/001_init.sql):
-- the palace knowledge graph's temporal triple (docs/schema.sql) extended with
-- deployment isolation and provenance. Timestamps are ISO-8601 TEXT here
-- rather than TIMESTAMPTZ; every other column and index matches, including the
-- partial unique index that enforces at-most-one-open-row per triple.

CREATE TABLE IF NOT EXISTS facts (
    fact_id           TEXT PRIMARY KEY,
    deployment_id     TEXT NOT NULL,
    subject           TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object            TEXT NOT NULL,
    valid_from        TEXT,
    valid_to          TEXT,
    confidence        REAL NOT NULL DEFAULT 1.0,
    engineer_id       TEXT NOT NULL,
    source_session_id TEXT,
    schema_version    TEXT NOT NULL,
    recorded_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_subject   ON facts(deployment_id, subject);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(deployment_id, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_object    ON facts(deployment_id, object);
CREATE INDEX IF NOT EXISTS idx_facts_valid     ON facts(deployment_id, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_recorded  ON facts(deployment_id, recorded_at DESC, fact_id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_facts_open_triple
    ON facts(deployment_id, subject, predicate, object)
    WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS api_keys (
    key_id        TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    deployment_id TEXT NOT NULL,
    engineer_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    revoked_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_keys_deployment ON api_keys(deployment_id);
