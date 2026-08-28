# Architecture

Two deployment units and a shared wire format. The engineer's palace is
private and local; the team's agora is shared and remote; `contracts` is the
only code both sides import.

```
┌─ engineer's machine ───────────────────────────────┐
│                                                    │
│  Claude Code (or any MCP agent)                    │
│     │                                              │
│     ├── palace tools ──► ChromaDB  (verbatim,      │
│     │                     wing/room/drawer)        │
│     │                                              │
│     └── memagora_* tools ──► audit.jsonl  ◄── every│
│                    │          crossing, always     │
│                    │                               │
│                    ▼                               │
│              mempalace/client.py                   │
└────────────────────┼───────────────────────────────┘
                     │  HTTPS, Bearer <api key>
                     │  facts + decisions only
                     ▼
┌─ the team's agora ─────────────────────────────────┐
│  agora/api  ──► agora/auth (key → deployment)      │
│             └─► AgoraStore  ──► Postgres | SQLite  │
└────────────────────────────────────────────────────┘
```

## Why the palace and the agora are separate

They answer different questions and have opposite privacy properties.

The palace is one engineer's stream of consciousness, stored verbatim, on their
disk. Its value is total recall of what *they* did. Sharing it would be both a
privacy problem and useless noise — nobody wants a colleague's debugging.

The agora is what the team agreed on, structured, on a server. Its value is
that it outlives the session, the engineer, and the project's memory of why.

Nothing moves between them automatically. An agent decides, per item, that
something belongs to the team and calls a tool. That decision point is the
whole design.

## The boundary

Three mechanisms, deliberately overlapping:

1. **Structure.** Only triples and decisions cross. There is no endpoint that
   accepts a conversation chunk, so no bug can send one.
2. **Audit.** Every emission is written to `~/.mempalace/audit.jsonl` before
   the network call, whether or not the call happens. The engineer can always
   answer "what has left my machine".
3. **Dry run, on by default.** A configured deployment sends nothing until the
   engineer explicitly turns dry-run off.

The prose fields on a decision are the one place free text crosses. The server
caps them (4000 characters, 20 list entries) so the boundary is enforced rather
than trusted — see [agent-integration.md](agent-integration.md).

## Request path

`POST /ingest` is the write path. One batch carries decisions, closes, and
facts, and the server applies them **in that order**:

1. **Decisions** — so a fact carrying `decision_id` never lands pointing at
   something that is not there yet.
2. **Closes** — so a replacement fact does not collide with the open row it
   supersedes.
3. **Facts.**

Partial acceptance throughout: a bad fact is counted and skipped, not fatal to
the batch. The batch is refused whole only when the envelope is unusable — an
unsupported schema version, or more items than `AGORA_MAX_BATCH`.

## Identity and isolation

The API key is the only source of `deployment_id` and `engineer_id`. Neither is
ever read from a request body, and every storage method takes `deployment_id`
as an argument — a store implementation is structurally incapable of answering
an unscoped query.

One team runs one deployment with one database. The `deployment_id` column is
the second line of defence: even a misconfiguration pointing two teams at one
database could not let one read the other's facts.

## Temporal model

Facts are triples with validity bounds, following the SCD Type 2 pattern the
palace's knowledge graph already used:

- `valid_from` / `valid_to` are nullable ISO-8601 text, compared
  lexicographically. NULL means unbounded.
- `valid_to IS NULL` means the fact currently holds.
- As-of queries are inclusive on both ends.
- A partial unique index enforces at most one *open* row per
  `(deployment_id, subject, predicate, object)`.

Facts are never deleted. Superseding one sets `valid_to`, which is what keeps
"we used SQS until September" answerable after the switch to Kinesis.

Two open facts sharing a subject and predicate are not an error — a service can
have two owners — so the server stores them and the read tools *report* the
ambiguity rather than resolving it.

## Storage abstraction

`AgoraStore` (`agora/storage/base.py`) is the seam, following the conventions
RFC 001 set for the palace side: kwargs-only signatures, `ClassVar` metadata,
frozen result types, lazy I/O, no driver types crossing the boundary.

Postgres is the reference implementation. SQLite ships alongside it and passes
the same conformance suite, which is what keeps the abstraction honest — an
interface with one implementation is just that implementation's shape. A
third-party backend registers under the `agora.stores` entry-point group and
proves itself by subclassing
`agora.storage.testing.AbstractStoreContractSuite`.

## Versioning

Three things version independently:

| Unit | Versioned by | Why |
|---|---|---|
| `contracts` | its own `pyproject.toml` + `SCHEMA_VERSION` | Palace and agora upgrade on different schedules |
| `agora` | its own `pyproject.toml` | Deployed per team, on the team's schedule |
| `mempalace` | the root `pyproject.toml` | Installed per engineer |

Every payload carries `schema_version`. Same major, any minor is accepted and
unknown fields ignored, so a client one release ahead still works. A newer
major is refused rather than misinterpreted.

## Failure behavior

Nothing in the palace-side path raises. `client.py` returns a response object
reporting the batch as rejected; the MCP tools return an error dict; the hooks
record and move on. A team server outage must never end an engineer's session,
and the audit log plus `mempalace audit resend` is how the facts survive it.

On the server, a storage failure is a `503` with a generic message — the DSN
never reaches a client — and an un-migrated schema stops the process at startup
rather than failing every write.

## What this does not do

- **No multi-device palace sync.** That problem is the agora's, not file
  replication's.
- **No automatic propagation.** Nothing crosses without an explicit tool call.
- **No search over rationale text.** The agora is a knowledge graph with
  reasoning attached, not a document store.
- **No retrieval-accuracy claims.** MemAgora's value is institutional memory
  across engineers; palace retrieval is inherited as-is.
