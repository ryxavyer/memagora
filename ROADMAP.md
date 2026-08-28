# MemAgora Roadmap

> MemAgora is a team knowledge graph layer built on a selectively-inherited fork of [MemPalace](https://github.com/MemPalace/mempalace). See [AGENTS.md](AGENTS.md) for the project thesis and [FOUNDATION.md](FOUNDATION.md) for which inherited subsystems are on MemAgora's path versus dormant.
>
> This roadmap covers MemAgora's own milestones plus the inherited stability work we still depend on. It does **not** cover MemPalace's roadmap items that aren't on MemAgora's critical path (LanceDB, PalaceStore, Postgres-as-palace-backend, Synapse MMR, Qdrant, multi-device sync, multilingual embeddings, time-decay scoring). Those remain MemPalace's concerns.

## Current state

The repo today contains MemPalace's code with MemAgora documentation layered on top. Concretely:

- The Python package is still named `mempalace` in [pyproject.toml](pyproject.toml). The directory is still `mempalace/`. Both rename to `palace/` and the CLI renames `mempalace` → `memagora` at v1.0 — deliberately deferred to keep upstream cherry-picking cheap until MemAgora is stable. See "v1.0 — the great rename" below.
- `contracts/` (v0.1) and `agora/` (v0.3) now exist as described in [AGENTS.md](AGENTS.md), each independently versioned with its own `pyproject.toml`. New MemAgora-specific *engineer-side* code (classifier, client, audit, config, backend wrapper) still lands inside the existing `mempalace/` package until the rename.
- Inherited MemPalace subsystems range from "actively used" to "strip candidate." The audit and the strip/keep status of each subsystem is in [FOUNDATION.md](FOUNDATION.md).

**Target structure (final state):**

```
palace/      ← engineer-side code: foundation + classifier + client + audit + config
             (currently named mempalace/; rename + content stays put at v1.0)
agora/       ← deployable team server (own pyproject.toml, separate dependency profile)
contracts/   ← wire format shared between palace and agora — independently versioned
hooks/  docs/  tests/
```

Two top-level code directories per deployment unit, plus a neutral `contracts/` for the shared HTTP schema. "memagora" is the project / repo / CLI name, not a code directory.

Until the rename, file paths in this roadmap and in [FOUNDATION.md](FOUNDATION.md) reference `mempalace/` because that's what's on disk. Read "the foundation" and "palace/" as interchangeable.

**Pre-rename mechanics:** new MemAgora-specific code (classifier, client, audit, etc.) goes into `mempalace/` directly — `mempalace/classifier.py`, `mempalace/client.py`, etc. Adding a sibling `palace/` package now would create two install paths that have to be unwound at v1.0. Keeping one package and renaming it once is cleaner. The lineage of which files were inherited vs. newly written is captured in per-file provenance headers added during the rename PR.

## Inherited stability — what we depend on from upstream

These items from MemPalace's own work are on MemAgora's critical path. They are tracked here so we don't accidentally lose them when we prune.

**Already merged and load-bearing:**

- Backend storage seam (#413) — `mempalace/backends/base.py`. This is MemAgora's primary integration point; our backend extends `BaseBackend`/`BaseCollection`. Interface must remain stable across upstream bumps.
- Query sanitization (#385) — applied to classifier inputs before the LLM sees them.
- Security hardening + KG threading locks + WAL permission fixes (#647) — our hooks fire concurrent KG writes; we need these.
- MCP drawer CRUD, paginated export, hook settings (#667) — we extend the MCP tool surface and hooks-cli on top of these.
- MCP ping health check (#600) — needed for daemon-mode probes from the agora server.

**Stability fixes we want before we cut a MemAgora release:**

- HNSW index bloat prevention (#346) — engineers will accumulate real palaces; the unmitigated 441 GB regression is a deployment blocker.
- Auto-repair BLOB seq_ids from chromadb 0.6→1.5 migration (#664) — defensive against engineers bringing in palaces from earlier versions.
- Stale index detection / HNSW reconnect (#663) — long-lived sessions with background hooks will trip this.
- Hybrid search keyword fallback (#662) — we use the in-palace search path for engineer recall before the agora is consulted.

**Process:** bug fixes to subsystems on this list should be considered for upstream contribution via the `upstream` remote. Bug fixes to subsystems FOUNDATION.md flags as strip candidates (AAAK dialect, Layer 1 importance, `_fuzzy_match`, hardcoded dedup stats) are not worth contributing — strip them locally.

## v0.1 — Skeleton (shipped 2026-05-03)

Goal: stand up the new package layout and the backend-wrapper integration point. No network calls yet.

- ✓ Stripped the four FOUNDATION-flagged dead subsystems: AAAK dialect (`mempalace/dialect.py` deleted, `mempalace compress` CLI and `mempalace_get_aaak_spec` MCP tool removed), Layer 1 importance scoring (`MemoryStack` simplified to L0/L2/L3), `palace_graph.py:_fuzzy_match` (replaced with inline substring filter at the only caller), hardcoded dedup statistics (`show_stats()` and `--stats` flag removed; `dedup_palace()` itself untouched).
- ✓ Documented the upstream-sync workflow (fetch `upstream/main` → fast-forward `mempalace-main` → audit diff → selective merge into `master`) and the version-pinning policy in [AGENTS.md](AGENTS.md) "Known Risks."
- ✓ Added MemAgora-specific modules inside the existing `mempalace/` package: `classifier.py` (stub returning `[]`), `client.py` (stub, no httpx/requests dep), `audit.py` (real append-only JSONL log), `config_agora.py` (`AgoraConfig` + env-var precedence), and `backend_agora.py` wrapper. Suffixed names drop at the v1.0 rename.
- ✓ Created top-level `contracts/` package with the wire format. Independently versioned with its own `pyproject.toml`. Pure dataclasses, no runtime dependencies.
- ✓ Implemented `mempalace/backend_agora.py` as a `BaseBackend` wrapper around `ChromaBackend`. Reads pass through; `add`/`upsert` write one audit entry per document when an endpoint is configured. Classifier hook is the v0.2 plug-in point at `_maybe_audit`.
- ✓ Configuration layer: endpoint URL, API key, classifier prompt path, dry-run flag. All optional — `endpoint=None` makes the wrapper a pure passthrough with no audit entries.
- ✓ Dry-run mode is the default. Audit log records intended writes without making any network call. Stays as the integration-test mode forever.
- ✓ Wired into `palace.py:_resolve_default_backend` via `backends.registry.resolve_backend_for_palace()`. Engineers opt in by setting `MEMPALACE_BACKEND=agora`. Default remains `chroma` when unset.

**Verification:** 1483 tests passing, 0 failures. Smoke test: `MEMPALACE_BACKEND=agora MEMPALACE_AGORA_ENDPOINT=http://example.invalid python -c "from mempalace.palace import get_collection; ..."` produces audit-log entries without network activity.

## v0.2 — Classifier + audit (shipped 2026-05-15)

Goal: the local half of the palace-to-agora pipe works end to end on the engineer's machine.

- ✓ Real classifier (`mempalace/classifier.py`) replaces v0.1 stub. Calls the inherited `llm_client.get_provider()` (stdlib `urllib`, no new HTTP deps), parses JSON output into `FactPayload` objects, caps emission at `AgoraConfig.max_facts_per_turn`, swallows every error path (LLM failure, malformed response, missing key) and returns `[]` so nothing leaks on failure.
- ✓ Default conservative classifier prompt at `mempalace/classifier_prompts/default.md`. Documents decisions/contracts/deprecations/ownership as emit-worthy and exploration/debugging/hypotheticals as not. Configurable per deployment via `AgoraConfig.classifier_prompt_path`.
- ✓ `AgoraConfig` extended with LLM provider/model/endpoint/api-key plus `max_facts_per_turn` and `transcript_last_n`. Default is anthropic + claude-haiku-4-5; auto-reads `ANTHROPIC_API_KEY` so Claude Code users get zero-config classification.
- ✓ `mempalace classify <transcript>` CLI subcommand — used by hooks. Backgroundable; no-op when `MEMPALACE_AGORA_ENDPOINT` is unset.
- ✓ `mempalace audit` CLI with nested `tail` / `export` subactions (model: existing `mempalace hook run` pattern). `audit diff` deferred to v0.3 when a live agora exists to diff against.
- ✓ Audit log now distinguishes two entry families via `entry_type`: `drawer_write` (from v0.1 `AgoraCollection._maybe_audit`) and `classify` (new in v0.2, one per emitted FactPayload).
- ✓ Hook integration: `mempal_save_hook.sh` backgrounds `mempalace classify "$TRANSCRIPT_PATH" &` after mining; `mempal_precompact_hook.sh` runs it synchronously before compaction. Stays under the 500ms hook budget in the save path.
- ✓ Classifier eval harness at `tests/test_classifier_eval.py` with 7 fixture conversations (explicit decisions, ownership, SLAs, exploratory non-facts, debugging without resolution, hypotheticals, low-confidence dropping). Mocked-LLM layer runs in CI; live layer gated by the new `live` pytest marker (`pytest -m live`, requires `ANTHROPIC_API_KEY`).

**Verification:** 1554 tests passing (3 skipped, 7 live-marker deselected), `ruff check` and `ruff format --check` clean, all CLI subcommands dispatch correctly. The classifier reads recent transcript turns, calls Claude via the inherited LLM client, parses JSON into FactPayloads, and writes one `entry_type: "classify"` audit entry per fact — with no actual network POST to an agora server until v0.3 ships one.

## v0.3 — Reference agora server (shipped 2026-08-12)

Goal: a deployable team server with one reference storage backend.

- ✓ FastAPI server at `agora/`, with its own `pyproject.toml` (engineer-side installs never pull FastAPI or psycopg). `agora/Dockerfile` + `agora/docker-compose.yml` are the reference deployment; build context is the repo root because the image needs `contracts/` too.
- ✓ Storage abstraction at `agora/storage/base.py` following RFC 001's conventions (kwargs-only signatures, `ClassVar` metadata, frozen result types, lazy I/O). Two implementations ship: `postgres.py` (psycopg3, raw SQL, the reference) and `sqlite.py` (stdlib, local dev + the default CI suite). Selection via `AGORA_STORE`, with an `agora.stores` entry-point group for third-party backends — the server-side analogue of `MEMPALACE_BACKEND`.
- ✓ Schema at `agora/storage/migrations/postgres/001_init.sql` — the palace's temporal triple extended with `deployment_id` and provenance (`engineer_id`, `source_session_id`, `schema_version`, `recorded_at`). A partial unique index enforces at most one open row per triple, which the palace KG only enforced in application code. Migrations are numbered `.sql` files applied by a runner and recorded in `schema_migrations`; `AGORA_AUTO_MIGRATE` defaults off.
- ✓ HTTP API: `POST /facts` (partial acceptance — this pins the batch contract `contracts/api.py` left open), `GET /facts` with subject/predicate/object/as-of/current/confidence filters and keyset pagination, `GET /timeline`, `GET /health` (unauthenticated form reveals nothing about the deployment).
- ✓ Auth: per-engineer API keys (`ak_<id>.<secret>`, SHA-256 of a 128-bit secret stored, never the secret). The key is the only source of `deployment_id` and `engineer_id` — neither is read from a request body, so cross-deployment leakage is structurally impossible rather than a validation rule.
- ✓ Schema versioning: per-fact version overrides the envelope; same-major any-minor accepted with unknown fields ignored; newer major refused with `schema_version_unsupported`; a `MIGRATIONS` table is the seam for accepting older majors later. `GET /health` advertises what the server accepts.
- ✓ `agora-admin` CLI: `migrate`, `issue-key`, `revoke-key`, `list-keys`, `stats`, and the `export`/`import` pair that is the storage-swap migration path (ids, ingest timestamps, and per-engineer provenance survive the move).
- ✓ [docs/deployment.md](docs/deployment.md) covers single-team self-hosting, key issuance, the engineer-side config, backups, upgrades, client/server skew, and changing storage backends.
- ✓ **Closing the loop** (deferred here from v0.2): `mempalace/client.py` is a real stdlib-`urllib` POST — no new engineer-side dependency, never raises, one retry on transport errors and 5xx. `cmd_classify` posts only when `dry_run` is off and records one `entry_type: "post"` audit entry per batch (endpoint, counts, outcome; never the API key). `mempalace audit diff` compares the local audit log against what the agora holds.
- ✓ Packaging fix: `contracts/` and `agora/` both declared `packages = ["<name>"]` against a flat layout, which builds an empty wheel. v0.3 is the first milestone that actually installs either one, so both now map the project root onto the import name.

**Verification:** 1754 palace-side tests passing (3 skipped), plus 213 agora tests — the storage conformance suite runs against both SQLite and a live Postgres, at 95% coverage of `agora/`. `ruff check` and `ruff format --check` clean. End to end on a real deployment: `docker compose up` → `agora-admin migrate` → `issue-key` → `mempalace classify` POSTs classified facts → `GET /facts` returns them → `mempalace audit diff` reconciles → a second deployment's key sees none of it.

## v0.4 — Round trip (shipped 2026-08-28)

Goal: agents drive agora population directly, agora facts feed back into the agent experience, and the graph tells the truth about time.

**Decisions taken before implementation:**

- **A decision gets its own table, not decomposed triples.** Title, rationale, alternatives, constraints, and open questions do not fit `(subject, predicate, object)` without stuffing paragraphs into a column meant for an entity name. `AgoraStore` widened once, deliberately, before any third party implements it.
- **Emission tools honor `dry_run`.** It defaults to `True`, so the primary population path is off until an engineer turns it on, and every tool result says in words that nothing was sent.

**Wire format — `contracts` 0.1.0 → 0.3.0:**

- ✓ `DecisionRecord` (0.2.0) — `decision_id`, `title`, `chosen`, `rationale`, `alternatives_rejected`, `constraints`, `open_questions`, `decided_on`. `FactPayload` gained optional `decision_id`; `IngestRequest` / `IngestResponse` / `GetDecisionsResponse` added.
- ✓ `FactClose` (0.3.0) — a request to end a fact, naming the triple in full because fact ids are server-generated and no palace has ever seen one. `IngestRequest.closes`, `IngestResponse.facts_closed`.
- ✓ Both bumps exercised the version negotiation v0.3 built: a 0.1.0 client still posts to `POST /facts` and reads back facts with `decision_id: null`.

**Agent-driven emission — the primary path:**

- ✓ `mempalace/mcp_agora.py` holds six MCP tools, merged into the palace's registry: `memagora_record_fact`, `memagora_record_decision`, `memagora_facts_about`, `memagora_timeline`, `memagora_decisions_about`, `memagora_why`. Kept in their own module because they talk to the team server, not the local palace.
- ✓ `memagora_record_decision` takes the facts a decision produced and links them automatically, so one call records both the conclusion and the argument.
- ✓ Every emission is mirrored to the audit log (`entry_type: "emit"`) *before* anything crosses, and `dry_run` short-circuits the network call while leaving the local record intact. Tool results say "recorded locally; NOT sent" in words the agent must repeat.
- ✓ Nothing raises: an unreachable agora, a rejection, even an unexpected exception from the client comes back as an error dict. A team outage cannot end an engineer's session.
- ✓ The hook-based `mempalace classify` path (v0.2) is retained as a fallback and is no longer primary.

**Superseding facts — the gap v0.3 left:**

- ✓ `AgoraStore.close_fact` sets `valid_to` on the open row matching a triple, mirroring the palace KG's `invalidate` including its default of today. Facts are never deleted, so "we used SQS until September" stays answerable.
- ✓ `POST /ingest` applies a batch in a fixed order — decisions, then closes, then facts — so a replacement never collides with the open row it supersedes and a linked fact never dangles.
- ✓ `memagora_record_fact(..., supersedes="SQS FIFO")` closes the old fact and opens the new one in one request, and warns when nothing matched rather than reporting success.
- ✓ Read-side contradiction surfacing: two open facts sharing a subject and predicate are reported by `memagora_facts_about` and `memagora_why` rather than resolved. Co-ownership is legitimate; the server cannot tell it from a stale answer, so it says so.

**Reading the agora back:**

- ✓ `client.get_facts` gained every filter the server implements; `client.get_timeline`, `client.get_decisions` and `client.post_ingest` added.
- ✓ `GET /decisions`, `GET /decisions/{id}`, and `decision_id` as a `/facts` filter.
- ✓ Wake-up integration: `mempalace wake-up` appends a team block, `--no-team` suppresses it. **The mapping question is resolved and written down** — a wing name matches a fact *subject* verbatim, and because that is narrow, a second unscoped block of the most recently recorded facts is included. "Time-bounded" is by count, since the agora returns newest-ingest-first. Never blocks: no agora, or an unreachable one, leaves wake-up exactly as it was.

**Operational debt cleared:**

- ✓ `mempalace audit resend` — the other half of the no-offline-queue decision. Compares the local log against the agora exactly as `audit diff` does and re-sends what is missing, so it is safe to run twice. `--dry-run` lists without sending.
- ✓ Optional `psycopg_pool` pooling behind `AGORA_POOL_SIZE` (the `pool` extra), confined to `PostgresStore._connection`, degrading loudly to one connection when the package is absent. The conformance suite runs a third time through the pool — if pooling changed any observable behavior, that suite would say so.
- ✓ [docs/agent-integration.md](docs/agent-integration.md) — what belongs in the agora, why a decision without its alternatives is just an assertion, where the prose boundary sits, and the `CLAUDE.md` snippet a deployment needs so its agents actually emit.
- ✓ [docs/architecture.md](docs/architecture.md) — the two deployment units, the three boundary mechanisms, request ordering, the temporal model, and what the system deliberately does not do.
- ✓ [docs/deployment.md](docs/deployment.md) gained the worked example: a decision on day 1, a teammate reading it on day 3 without asking anyone, a supersede in week 3, and what a forgotten supersede looks like.

**Also fixed, found while verifying:**

- ✓ The server refuses to start against an un-migrated schema (`pending_migrations` checked in `create_app`). Reproducing the real v0.3→v0.4 upgrade showed new code on an old schema comes up *healthy* and then fails every write inside a driver error. `docs/deployment.md` had the upgrade order backwards too.
- ✓ `agora-admin export` was about to silently drop decisions, which would have made a storage-backend swap lose exactly the reasoning this milestone adds. Both records now travel with a `_type` discriminator; a v0.3 dump without one still imports.
- ✓ `client.post_facts` was dropping `decision_id` on the wire — a hand-written field list, now `dataclasses.asdict`.
- ✓ `audit diff` and `audit resend` read both `classify` and `emit` entries; reading only one would have reported the other path's facts as missing.

**Verification:** 1888 palace-side tests passing (3 skipped), agora tests at 96% coverage, `ruff` clean. The storage conformance suite runs three times — SQLite, Postgres, pooled Postgres. End to end against the containerized server on real Postgres: dry-run recording locally and sending nothing; a decision and its facts emitted; a second engineer's `memagora_why` returning the rationale and rejected alternatives; a supersede leaving one current answer with the timeline intact; a forgotten supersede surfacing as a reported conflict; the server killed mid-session leaving the tool erroring rather than raising; and `audit diff` → `audit resend` closing the gap when it came back.

**Deliberately not done here:** the first pilot deployment, which is gated on TLS in front of the server (API keys are bearer tokens) and on a team willing to run it.

## v1.0 — General availability + the great rename

Goal: production-ready for self-hosted teams, and a final scrub of `mempalace` from the codebase.

**Product:**

- Hardened MCP emission and query tools with documented guidance for each deployment's agent instructions.
- A **third-party** agora storage backend, installed through the `agora.stores` entry point from outside this repo. In-tree SQLite and pooled Postgres already prove the interface has more than one shape (v0.3, v0.4); what is unproven is that someone else can implement it without patching core.
- Stability guarantees on the contracts schema and the agora HTTP contract.
- Documentation: architecture, agent-integration, deployment, operator runbook.

**The great rename** (one coordinated PR, after the product is stable):

The rename is deliberately last. Earlier, every upstream MemPalace fix is one `git cherry-pick` away. Post-rename, every upstream patch needs path rewrites. We pay that cost once, when MemAgora has diverged enough that wholesale upstream merges aren't realistic anyway.

- Directory `mempalace/` → `palace/`. Rename `palace/palace.py` (collision) to `palace/core.py` in the same PR.
- Drop `_agora` suffixes added in v0.1 from MemAgora-specific modules: `backend_agora.py` → `backend.py`, `config_agora.py` → `config.py`, etc. The original inherited `config.py` was already audited; resolve the namespace collision at the rename rather than carrying suffixes forever.
- Python package name in `pyproject.toml`: `mempalace` → `palace`. `agora` and `contracts` are sibling packages with their own metadata (`agora/pyproject.toml`, `contracts/pyproject.toml`).
- CLI: `mempalace` → `memagora`. MCP entry: `mempalace-mcp` → `memagora-mcp`.
- Entry-point group: `[project.entry-points."mempalace.backends"]` → `palace.backends`. Same for `mempalace.sources`.
- Hook env vars: `MEMPAL_PYTHON`, `MEMPAL_DIR`, `MEMPAL_VERBOSE` → `MEMAGORA_*`.
- Hook scripts in [hooks/](hooks/) renamed and shell-out commands updated (`mempalace mine` → `memagora mine`).
- State directory: `~/.mempalace/` → `~/.memagora/`. Auto-migration on first launch.
- Per-file upstream-provenance headers added at rename time so the fork lineage stays auditable post-rename. This replaces the directory boundary as the way to answer "did this come from upstream?"
- Bump major version. Document the upgrade path for existing engineers.
- After this PR, there should be zero remaining `mempalace` references in the codebase except in provenance headers and historical documentation.

## What we are deliberately not doing

- **No multi-device sync for the palace.** That problem is solved by the agora server, not by file replication.
- **No raw-content propagation across engineers.** The emission tools are the privacy boundary. Agents emit structured facts and decisions — never raw conversation chunks. If content is uncertain, it stays in the palace.
- **No silent network calls.** A MemAgora install with no configured endpoint behaves identically to MemPalace alone. The only network call is the explicit POST to the engineer's configured agora server.
- **No benchmark claims about retrieval accuracy.** MemAgora's value proposition is institutional memory across engineers, not LongMemEval scores. We inherit MemPalace's retrieval as-is and do not headline its benchmarks as MemAgora's.

## Branch model

```
master           ← active MemAgora development; PRs target here
mempalace-main   ← local mirror of upstream/main, advanced deliberately

upstream         ← git remote pointing at MemPalace (github.com/MemPalace/mempalace)
```

`mempalace-main` is the integration point for upstream sync. We fetch `upstream/main`, fast-forward `mempalace-main`, audit the diff, and merge selectively into `master`. We do not auto-track upstream.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. PRs target `master`. Bug fixes to inherited MemPalace plumbing on the "actively used" list in [FOUNDATION.md](FOUNDATION.md) should be considered for upstream contribution from a branch off `mempalace-main`; novel MemAgora logic stays on `master` in this repo.
