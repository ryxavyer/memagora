# memagora-agora

The team half of MemAgora: a small HTTP service that stores classified facts
from engineers' local palaces as a shared temporal knowledge graph.

Deployed **per team**. Each deployment is an independent service with its own
database — see [`docs/deployment.md`](../docs/deployment.md) for self-hosting.

## What crosses the boundary

Two shapes, both structured, both deliberate:

* **Facts** — `(subject, predicate, object)` triples with optional validity
  bounds, a confidence score, and provenance.
* **Decisions** — the reasoning that produced a set of facts: what was chosen,
  why, what was rejected, what is still open. Agent-authored prose, capped in
  length, linked to its facts by `decision_id`.

Raw conversation text never leaves an engineer's machine. The agent decides
what is worth sharing and emits it explicitly; the engineer's local audit log
mirrors everything that crossed.

## Local development

No Docker, no Postgres:

```bash
pip install -e ./agora
export AGORA_STORE=sqlite AGORA_SQLITE_PATH=./agora.sqlite3 AGORA_DEPLOYMENT_ID=dev
agora-admin migrate
agora-admin issue-key --engineer "$USER"
agora-server                    # http://0.0.0.0:8000
```

## Configuration

Every setting is an environment variable. There is no config file.

| Variable | Default | Meaning |
|---|---|---|
| `AGORA_STORE` | `postgres` | Storage backend name (`postgres`, `sqlite`, or an installed plugin) |
| `AGORA_DSN` | — | Postgres connection string. Required when `AGORA_STORE=postgres` |
| `AGORA_SQLITE_PATH` | `agora.sqlite3` | Database file when `AGORA_STORE=sqlite` |
| `AGORA_DEPLOYMENT_ID` | `default` | This deployment's identity; stamped on every fact and key |
| `AGORA_AUTO_MIGRATE` | `false` | Apply migrations at startup. Off by default — operators migrate deliberately |
| `AGORA_POOL_SIZE` | `0` | Postgres connections to pool. `0` = one lock-guarded connection. Needs the `pool` extra |
| `AGORA_MAX_BATCH` | `100` | Maximum items in one `POST /facts` or `POST /ingest` |
| `AGORA_MAX_LIMIT` | `500` | Ceiling on the `limit` query parameter |
| `AGORA_PAGE_LIMIT` | `100` | Default page size |
| `AGORA_HOST` / `AGORA_PORT` | `0.0.0.0` / `8000` | Bind address |
| `AGORA_LOG_LEVEL` | `info` | Log level |

## API

All endpoints except `GET /health` require `Authorization: Bearer <key>`. The
key determines the deployment and the engineer identity; neither is ever read
from a request body.

### `POST /facts`

```json
{"facts": [{"subject": "auth-service", "predicate": "owned_by",
            "object": "platform-team", "valid_from": "2026-05-01",
            "confidence": 0.9, "source_session_id": "abc123"}],
 "schema_version": "0.1.0"}
```

Returns `{"accepted": 1, "rejected": 0, "message": null}`.

**Partial acceptance.** Facts that fail validation or duplicate an existing
open triple are counted in `rejected`; the rest are stored. The whole batch is
refused only when the envelope is unusable — `400` for an unsupported
`schema_version`, `413` for more than `AGORA_MAX_BATCH` facts.

### `POST /ingest`

The path agent-driven emission uses: one batch carrying a decision and the
facts it produced, so the decision is stored first and a fact's `decision_id`
never points at something that is not there yet.

```json
{"decisions": [{"decision_id": "dec-queue-2026-08",
                "title": "Queue for the notifications service",
                "chosen": "SQS FIFO",
                "rationale": "Per-recipient ordering is a hard requirement.",
                "alternatives_rejected": ["Kafka — operational surface too large"],
                "constraints": ["Stay inside the existing AWS account"],
                "open_questions": ["Do we need a DLQ before launch?"],
                "decided_on": "2026-08-01"}],
 "facts": [{"subject": "notifications-service", "predicate": "uses",
            "object": "SQS FIFO", "decision_id": "dec-queue-2026-08"}],
 "schema_version": "0.2.0"}
```

Returns `{"facts_accepted", "facts_rejected", "decisions_accepted",
"decisions_rejected", "message"}` — the two halves are counted separately
because an agent needs to know which one the server kept.

Partial acceptance as on `POST /facts`. A `decision_id` that already exists is
rejected rather than overwritten: a decision records what was decided at a
moment, and silently rewriting one would make the history unreliable in exactly
the way the agora exists to prevent. Prose fields are capped (4000 characters
for a rationale or list entry, 20 entries per list) — the privacy boundary is
enforced, not merely intended.

The whole batch counts against `AGORA_MAX_BATCH`.

### `GET /decisions`

`?ids=d_1,d_2&limit=&cursor=`. Newest ingest first. There is no search over
rationale text, deliberately — this is a knowledge graph with reasoning
attached, not a document store.

### `GET /decisions/{decision_id}`

One decision, or `404`. This is what answers "why is it this way": find facts
about a subject, take their `decision_id`, fetch the argument behind them.

### `GET /facts`

Filters: `subject`, `predicate`, `object`, `as_of` (ISO date, inclusive both
ends), `current` (only facts with no end bound), `min_confidence`,
`decision_id` (what one decision produced), `limit`, `cursor`. Newest ingest
first; `next_cursor` is opaque.

Each fact carries the nine wire fields plus `fact_id`, `engineer_id`,
`recorded_at`, and `current`.

### `GET /timeline`

Ascending `valid_from`, unbounded starts last. `subject` matches either end of
the triple, so asking about a service surfaces both what it owns and what
depends on it.

### `GET /health`

Unauthenticated: `{"status", "version", "schema_versions"}` — enough for a
container healthcheck, nothing about the deployment. With a key: store backend,
latency, deployment id, and fact count.

### Errors

Always `{"error": "<stable_code>", "message": "..."}`. Codes: `unauthorized`,
`invalid_request`, `invalid_cursor`, `schema_version_unsupported`,
`batch_too_large`, `storage_unavailable`, `not_found`.

## Schema versioning

Every payload carries a `schema_version`; a per-fact value overrides the
envelope. Same major, any minor is accepted and unknown fields are ignored, so
a client one release ahead still works. A newer major is refused with a clear
message rather than stored under a changed interpretation.

## Migrations

The server **refuses to start** when the database has migrations it has not
applied. New code against an old schema otherwise comes up healthy and then
fails every write inside a driver error; failing at startup says what to run
while the operator is still watching the deploy.

```
agora-admin migrate       # apply pending migrations
```

`AGORA_AUTO_MIGRATE=1` migrates on boot instead — convenient for development,
not the default for a deployment holding a team's history.

## Storage backends

`AgoraStore` (`agora/storage/base.py`) is the seam. Postgres is the reference
implementation; SQLite ships for local development and small deployments.
A third-party backend registers itself under the `agora.stores` entry-point
group and proves conformance by subclassing
`agora.storage.testing.AbstractStoreContractSuite`.

Moving between backends is `agora-admin export` → `agora-admin import`; ids,
ingest timestamps, and per-engineer provenance are preserved.
