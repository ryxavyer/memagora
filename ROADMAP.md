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

## v0.4 — Round trip

Goal: agents drive agora population directly, agora facts feed back into the agent experience, and the graph tells the truth about time.

**Agent-driven emission (primary path):**

MemAgora does not manage its own LLM pipeline. The agent calling the tools is the intelligence. Agora population happens through the agent explicitly calling emission MCP tools during a session — no separate LLM call re-processes content the agent already understood.

- New MCP emission tools: `memagora_record_fact`, `memagora_record_decision`. These are the primary agora population path.
- `memagora_record_decision` accepts the `DecisionRecord` shape — title, chosen approach, rationale, alternatives rejected, constraints, open questions — and links to the atomic facts it produced via a shared `decision_id`. This is the shape that answers "why was this decision made" when an agent queries the agora in a future session.
- `DecisionRecord` added to `contracts/facts.py` alongside `FactPayload`; `FactPayload` gets an optional `decision_id`. New `POST /ingest` endpoint accepts a mixed batch; existing `POST /facts` preserved for backwards compatibility.
- The hook-based `mempalace classify` path (v0.2) is retained as a fallback for agents that do not call emission tools, but is no longer the primary path.

**Reading the agora back:**

- Extend `client.get_facts` with the filters the server already implements — subject/predicate/object, `as_of`, `current`, `min_confidence`. v0.3 shipped it with `limit`/`cursor` only, which is enough for `audit diff` and not enough for anything an agent would ask.
- MCP query tools: `memagora_facts_about`, `memagora_timeline`, `memagora_decisions_about`, `memagora_why(subject, predicate)`. Discoverable via `mempalace_list_agents` pattern; no system-prompt bloat.
- Wake-up integration: team agora facts surface alongside palace context at session start. Time-bounded, scoped by current project/wing. **Open design question first:** the agora stores subject/predicate/object and the palace organizes by wing/room/drawer. Nothing maps the two today. Decide that mapping before writing the integration — the alternative is a scoping rule that quietly returns the wrong team's context.

**Superseding facts** — the gap v0.3 left, and the one that matters most before a pilot:

The temporal model is fully built server-side (`valid_from` / `valid_to`, as-of queries, one-open-row-per-triple) and nothing uses it. There is no way to close a fact: no PATCH, no DELETE, no equivalent of the palace KG's `invalidate()`. So "we moved off SQS FIFO to Kinesis" writes a *second* open row — a different object is a different triple, so the uniqueness index does not catch it — and both decisions sit there current and contradictory. For a system whose value proposition is institutional memory, last year's decision quietly outliving last month's is the failure mode that matters.

- Agents emit `valid_to` when recording a reversal via `memagora_record_fact`, and `valid_from` when a fact carries a known date.
- A server-side close operation, so a superseding fact ends the one it replaces in the same request rather than racing it.
- Failing that, or alongside it: surface contradictions at read time, so an agent that finds two open facts on the same `(subject, predicate)` says so rather than picking one.

**Operational debt carried out of v0.3:**

- **No offline retry.** A failed POST is recorded in the audit log (`entry_type: "post"`, `ok: false`) and dropped. `audit diff` surfaces the gap; `mempalace audit resend` is the missing half.
- **One Postgres connection**, lock-guarded — correct for a single uvicorn worker, thin for a pilot with real concurrency. `psycopg_pool` is a drop-in change confined to `PostgresStore._connection`.
- **CI has never actually run.** It did not trigger on `master` until v0.3 fixed the workflow triggers, so every v0.1–v0.3 "tests passing" claim was verified locally. The first push to `master` is the real check; treat a red first run as expected rather than alarming.
- **`docs/architecture.md` and `docs/agent-integration.md`** are promised by [AGENTS.md](AGENTS.md) and do not exist. The agent integration doc is the load-bearing one: it needs to explain how agents should call the emission tools, what belongs in a `DecisionRecord`, and how to configure a deployment's CLAUDE.md to encourage emission at decision points.

**Then:**

- End-to-end deployment guide with a worked example — [docs/deployment.md](docs/deployment.md) covers self-hosting mechanics as of v0.3; what is missing is the narrative walkthrough with a real team's facts in it.
- First pilot deployment. Gated on the supersede work and on TLS in front of the server (API keys are bearer tokens).

## v1.0 — General availability + the great rename

Goal: production-ready for self-hosted teams, and a final scrub of `mempalace` from the codebase.

**Product:**

- Hardened MCP emission and query tools with documented guidance for each deployment's agent instructions.
- At least one alternative agora storage backend implementation (proves the abstraction).
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
