# memagora-agora

The team half of MemAgora: a small HTTP service that stores classified facts
from engineers' local palaces as a shared temporal knowledge graph.

Deployed **per team**. Each deployment is an independent service with its own
database — see [`docs/deployment.md`](../docs/deployment.md) for self-hosting.

## What crosses the boundary

Structured triples only. `(subject, predicate, object)` with optional validity
bounds, a confidence score, and provenance. Raw conversation text never leaves
an engineer's machine; the classifier on the palace side decides what is worth
sharing and the engineer's local audit log mirrors every fact that crossed.

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
| `AGORA_MAX_BATCH` | `100` | Maximum facts in one `POST /facts` |
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

### `GET /facts`

Filters: `subject`, `predicate`, `object`, `as_of` (ISO date, inclusive both
ends), `current` (only facts with no end bound), `min_confidence`, `limit`,
`cursor`. Newest ingest first; `next_cursor` is opaque.

Each fact carries the eight wire fields plus `fact_id`, `engineer_id`,
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

## Storage backends

`AgoraStore` (`agora/storage/base.py`) is the seam. Postgres is the reference
implementation; SQLite ships for local development and small deployments.
A third-party backend registers itself under the `agora.stores` entry-point
group and proves conformance by subclassing
`agora.storage.testing.AbstractStoreContractSuite`.

Moving between backends is `agora-admin export` → `agora-admin import`; ids,
ingest timestamps, and per-engineer provenance are preserved.
