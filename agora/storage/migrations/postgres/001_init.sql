-- MemAgora fact store — Postgres reference schema.
--
-- Derived from the palace knowledge graph's temporal triple table (see
-- mempalace/knowledge_graph.py; docs/schema.sql is the same shape but has
-- drifted from the live code) and extended for the team setting:
--
--   deployment isolation : deployment_id on every row, always filtered
--   provenance           : engineer_id, source_session_id, schema_version,
--                          recorded_at
--
-- The palace's source_closet / source_file columns are deliberately absent —
-- they name palace-local storage that never crosses the boundary.
--
-- SCD Type 2 semantics, identical to the palace KG: valid_from / valid_to are
-- nullable ISO-8601 TEXT compared lexicographically, NULL means unbounded, and
-- valid_to IS NULL means the fact currently holds.

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
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_facts_subject   ON facts(deployment_id, subject);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(deployment_id, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_object    ON facts(deployment_id, object);
CREATE INDEX IF NOT EXISTS idx_facts_valid     ON facts(deployment_id, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_recorded  ON facts(deployment_id, recorded_at DESC, fact_id DESC);

-- At most one open row per triple per deployment. The palace KG enforces this
-- in application code; here the database does it, so a race between two
-- engineers posting the same decision cannot produce two open rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_facts_open_triple
    ON facts(deployment_id, subject, predicate, object)
    WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS api_keys (
    key_id        TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    deployment_id TEXT NOT NULL,
    engineer_id   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_deployment ON api_keys(deployment_id);
