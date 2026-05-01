# MemAgora Roadmap

> MemAgora is a team knowledge graph layer built on a selectively-inherited fork of [MemPalace](https://github.com/MemPalace/mempalace). See [AGENTS.md](AGENTS.md) for the project thesis and [FOUNDATION.md](FOUNDATION.md) for which inherited subsystems are on MemAgora's path versus dormant.
>
> This roadmap covers MemAgora's own milestones plus the inherited stability work we still depend on. It does **not** cover MemPalace's roadmap items that aren't on MemAgora's critical path (LanceDB, PalaceStore, Postgres-as-palace-backend, Synapse MMR, Qdrant, multi-device sync, multilingual embeddings, time-decay scoring). Those remain MemPalace's concerns.

## Current state

The repo today contains MemPalace's code with MemAgora documentation layered on top. Concretely:

- The Python package is still named `mempalace` in [pyproject.toml](pyproject.toml). The directory is still `mempalace/`. Both rename to `palace/` and the CLI renames `mempalace` → `memagora` at v1.0 — deliberately deferred to keep upstream cherry-picking cheap until MemAgora is stable. See "v1.0 — the great rename" below.
- `agora/` and `contracts/` directories described in [AGENTS.md](AGENTS.md) do not exist yet. New MemAgora-specific engineer-side code (classifier, client, audit, config, backend wrapper) lands inside the existing `mempalace/` package until the rename.
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

## v0.1 — Skeleton (next)

Goal: stand up the new package layout and the backend-wrapper integration point. No network calls yet.

- Strip the four FOUNDATION-flagged dead subsystems: AAAK dialect (`mempalace/dialect.py`), Layer 1 importance scoring, `palace_graph.py:_fuzzy_match`, hardcoded dedup statistics. Each is a separate PR with the rationale linked back to FOUNDATION.md.
- Document the upstream-sync workflow (fetch `upstream/main` → fast-forward `mempalace-main` → audit diff → selective merge into `master`) and the version-pinning policy in [AGENTS.md](AGENTS.md) "Known Risks." The `upstream` remote and `mempalace-main` branch already exist.
- Add the new MemAgora-specific modules inside the existing `mempalace/` package: `classifier.py`, `client.py`, `audit.py`, `config_agora.py`, and a `backend_agora.py` wrapper. Stubs and tests, no behavior yet. (Suffixed names avoid collisions with existing inherited files like `config.py`. They lose the suffix at the v1.0 rename when the foundation is also renamed and namespaces clarify.)
- Create top-level `contracts/` package with the wire format (fact payload, API request/response shapes). Independently versioned with its own `pyproject.toml` so a future third-party client can install it without pulling palace or agora.
- Implement `mempalace/backend_agora.py` as a `BaseBackend` wrapper around `ChromaBackend`. Pass-through writes today; classifier hook in v0.2.
- Configuration layer: per-engineer endpoint URL, API key, classifier prompt path. All optional — if unset, the wrapper is a no-op and the local palace is unchanged.
- Dry-run mode that logs what the classifier *would have* sent without making any network call. This is the default in v0.1 and stays as the integration-test mode forever.

## v0.2 — Classifier + audit

Goal: the local half of the palace-to-agora pipe works end to end on the engineer's machine.

- Default conservative classifier prompt with documented heuristics for what counts as a team-relevant fact (decisions, contracts, deprecations, ownership) versus what stays in the palace (raw conversation, exploration, debugging).
- Local audit log: every classifier output, whether posted or not, mirrored to a local file the engineer can inspect. Append-only, JSONL.
- `memagora audit` CLI: `tail`, `export`, `diff` (compare what was sent vs. what the agora returns).
- Test harness: fixture conversations with expected classifier outputs. Treat the classifier as a model under evaluation, not a black box.
- Hook integration: Stop and PreCompact hooks fire the classifier on the just-completed turn(s). Performance budget: classifier runs in the background, no chat-window tokens, no impact on the existing 500ms hook budget.

## v0.3 — Reference agora server

Goal: a deployable team server with one reference storage backend.

- FastAPI server skeleton at `agora/`. Single-binary deployment via Docker; `docker-compose.yml` reference. The server has its own `pyproject.toml` so engineer-side installs don't pull FastAPI/Postgres deps.
- Storage abstraction (mirroring the spirit of RFC 001 on the read side) with a Postgres reference implementation. Schema is the temporal triple model from [docs/schema.sql](docs/schema.sql) extended with deployment isolation and provenance fields.
- HTTP API: `POST /facts`, `GET /facts` with subject/predicate/object/time filters, `GET /timeline`, `GET /health`.
- Auth: per-engineer API keys, scoped to a single deployment. No cross-deployment data leakage by construction.
- Schema versioning on fact payloads so the server can evolve without breaking older clients.
- Deployment doc covering single-team self-hosting, including the migration story when the team switches storage backends.

## v0.4 — Round trip

Goal: agora facts feed back into the agent experience.

- Wake-up integration: team agora facts surface alongside palace context at session start. Time-bounded, scoped by current project/wing.
- MCP tools to query the agora directly from inside an agent session: `memagora_facts_about`, `memagora_timeline`, `memagora_decisions_in`. Discoverable via `mempalace_list_agents` pattern; no system-prompt bloat.
- End-to-end deployment guide with a worked example.
- First pilot deployment.

## v1.0 — General availability + the great rename

Goal: production-ready for self-hosted teams, and a final scrub of `mempalace` from the codebase.

**Product:**

- Hardened classifier with deployment-tunable prompts and a documented evaluation methodology each team can run against their own corpus.
- At least one alternative agora storage backend implementation (proves the abstraction).
- Stability guarantees on the classifier output schema and the agora HTTP contract.
- Documentation: architecture, deployment, classifier-tuning, operator runbook.

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
- **No raw-content propagation across engineers.** The classifier boundary is the privacy guarantee. If a fact's classification is uncertain, it stays in the palace.
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
