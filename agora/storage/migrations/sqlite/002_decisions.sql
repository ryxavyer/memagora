-- Decisions — SQLite variant. Same shape as the Postgres reference
-- (migrations/postgres/002_decisions.sql); recorded_at is ISO-8601 TEXT here.
--
-- A decision is not a triple. Title, rationale, alternatives, constraints,
-- and open questions do not decompose into subject/predicate/object without
-- stuffing paragraphs into a column meant for an entity name, so decisions
-- get their own table and facts link to them by decision_id.
--
-- The list-valued columns are JSON arrays of strings. They are read and
-- written whole, never queried element-wise, so a JSON text column is the
-- honest representation rather than three more join tables.

CREATE TABLE IF NOT EXISTS decisions (
    decision_id            TEXT NOT NULL,
    deployment_id          TEXT NOT NULL,
    engineer_id            TEXT NOT NULL,
    title                  TEXT NOT NULL,
    chosen                 TEXT NOT NULL,
    rationale              TEXT NOT NULL,
    alternatives_rejected  TEXT NOT NULL DEFAULT '[]',
    constraints_json       TEXT NOT NULL DEFAULT '[]',
    open_questions         TEXT NOT NULL DEFAULT '[]',
    decided_on             TEXT,
    source_session_id      TEXT,
    schema_version         TEXT NOT NULL,
    recorded_at            TEXT NOT NULL,
    -- Scoped primary key: decision ids are chosen by the emitting agent, so
    -- two deployments picking the same id must not collide.
    PRIMARY KEY (deployment_id, decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_recorded
    ON decisions(deployment_id, recorded_at DESC, decision_id DESC);

-- Facts produced by a decision point back at it. Nullable: most facts record
-- a state of the world rather than a choice.
ALTER TABLE facts ADD COLUMN decision_id TEXT;

CREATE INDEX IF NOT EXISTS idx_facts_decision ON facts(deployment_id, decision_id);
