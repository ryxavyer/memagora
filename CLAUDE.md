# CLAUDE.md

## Project Overview

This repository is **MemAgora** — a team knowledge graph layer built on a stripped-down fork of [MemPalace](https://github.com/MemPalace/mempalace), a local-first AI memory system.

MemPalace solves the problem of an individual engineer's reasoning, debates, and decisions evaporating between Claude Code sessions. MemAgora solves the problem of that context evaporating between engineers. Institutional knowledge that lived inside that AI session now lives somewhere a teammate can reach.

The core thesis: context saved in the palace improves the agent experience for the developer and context saved in the agora improves the agent experience for the whole team. Both can happen simultaneously while you work.

## What MemAgora Inherits From MemPalace (And What It Doesn't)

MemAgora is **selectively** built on MemPalace, not faithfully derived from it. Two things are worth keeping; much of the rest is not.

**What MemAgora inherits and depends on:**

- **Agent interface plumbing** — The MCP server, Claude Code hooks, conversation format parsers (Claude/ChatGPT/Codex/Slack), ChromaDB integration, and CLI dispatcher. This is real engineering work that would take weeks to rebuild and isn't algorithmically interesting to redo.
- **The organizational metaphor** — Wings, rooms, and drawers as a human-comprehensible mental model for navigating memory. This isn't an accuracy improvement over flat vector search (ChromaDB's defaults handle retrieval well on their own). The value is in *navigability and scoping* — an agent can search "how did we handle rate limiting" within a specific wing rather than against all of memory. Different problem than retrieval accuracy, real value.

**What MemAgora does not inherit:**

- **AAAK compression dialect** — Removed in v0.1. Demonstrated 12.4% accuracy regression for compression that didn't reliably save tokens at the scales engineers actually operate at.
- **Layer 1 "Essential Story" importance scoring** — Removed in v0.1. Sorted by metadata that the mining pipeline never set, making the output effectively random order.
- **Substring-as-fuzzy-matching** — Removed in v0.1. `palace_graph.py:_fuzzy_match` was `in` operator wrapped in misleading naming.
- **Hardcoded dedup statistics** — Removed in v0.1. `dedup.py` had estimated duplicates as `int(len(ids) * 0.4)` rather than computing them.
- **MemPalace's marketing claims** — The 96.6% LongMemEval score is largely ChromaDB's default behavior. The "memory palace architecture" is metadata strings on ChromaDB documents. MemAgora's value proposition is institutional memory, not benchmark performance, so none of this matters here.

**Trajectory:** Over time, MemPalace's footprint in MemAgora is expected to shrink. The plumbing stays. The organizational metaphor stays. Layers that don't earn their keep get stripped or replaced as MemAgora matures. The repo today inherits more of MemPalace than it will a year from now, and that's intentional.

> **For details on the MemPalace foundation as it currently exists in this repo — including which subsystems MemAgora actively uses, which it ignores, and which are candidates for removal — see [FOUNDATION.md](./FOUNDATION.md).**

## Mental Model

- Palace — your private memory. Verbatim, local, sovereign. Your raw stream of consciousness.
- Agora — the public square. Structured facts extracted from individual palaces and shared across the team. Curated, not raw.

The name comes from the Greek agora — the public square where citizens chose to bring ideas for collective benefit. The word "chose" matters. The agent itself decides what propagates from palace to agora, calling MemAgora's emission tools to record facts and decisions it judges to be team-relevant. Raw verbatim chunks stay in the palace. Only what the agent explicitly emits crosses the boundary.

## Design Principles

MemAgora inherits two MemPalace principles in modified form, and adds its own:

**Inherited (selectively):**

- **Local-first for the palace** — The engineer's local palace stores raw verbatim content and never sends it anywhere. MemAgora introduces a single explicit, configured network call to a team agora server — but that call carries only classified, structured facts, never raw user content. The local-first guarantee for the palace itself is intact.
- **Verbatim for raw storage** — Where MemAgora touches MemPalace's raw storage layer, the verbatim guarantee is preserved. MemAgora does not summarize raw conversation chunks.

**MemAgora-specific:**

- **Engineer sovereignty** — The local palace is private and stays private. MemAgora never reads or transmits raw verbatim chunks. It only propagates structured, classified facts the engineer's session produced. If in doubt, the fact stays in the palace.
- **Audit by default** — Every fact written to the team agora is mirrored to a local audit log the engineer can inspect. Engineers must always be able to see what left their machine.
- **Per-deployment isolation** — Each team or project group runs an independent MemAgora deployment with its own database. Project A's agora and Project B's agora share code but never share data or infrastructure.
- **Structured, not raw** — The agora stores knowledge graph triples with temporal validity. Decisions, contracts, deprecations. Never raw conversation chunks.
- **Optional, never invasive** — MemAgora is opt-in at the engineer level. An engineer can use MemPalace without MemAgora. The local experience is unchanged whether MemAgora is configured or not.
- **No silent network calls** — The only network call MemAgora makes is the explicit POST to the engineer's configured agora endpoint. No telemetry, no analytics, no fallback endpoints.

**Not inherited:** MemPalace's broader claims about 100% recall, the "method of loci" framing, and the implication that the wing/room/drawer structure produces algorithmic improvement in retrieval. These are MemPalace's framing, not MemAgora's commitments.

## Architectural Approach

MemAgora is designed as a **deployable template** — the same codebase, deployed independently per team. Two deployment units connected by agent-driven tool calls:

    Agent (Claude Code or any LLM-based coding agent)
           │  reads palace context via MCP tools each session
           │  emits facts and decisions via MCP tools during the session
           ▼
    MemAgora MCP tools ───── palace: verbatim read/write, local only
                             agora writes: memagora_record_fact
                                           (supersedes= closes the old one),
                                           memagora_record_decision
                             agora reads:  memagora_facts_about,
                                           memagora_timeline,
                                           memagora_decisions_about,
                                           memagora_why
           │
           ▼
    Team Agora Server ─────── pluggable storage layer (Postgres default,
                              deployable swap for other backends)

**MemAgora is a tool that agents call, not a system that manages its own intelligence.** The agent calling the tools is the intelligence. During a session, the agent reads from its local palace for context and emits team-relevant facts and decisions to the agora via MCP tools — no separate LLM pipeline runs inside MemAgora itself. The palace integration continues through MemPalace's pluggable backend interface (`mempalace/backends/base.py`); the backend wrapper handles local writes and audit logging. Agora population is the agent's responsibility, not the framework's.

The agora server itself is a separate deployable unit. Each team stands up their own instance. The server's storage layer is also abstracted — Postgres is the default and reference implementation, but a team should be able to swap in MySQL, a different KG store, or whatever fits their existing infrastructure.

The core invariant: **regardless of deployment configuration, the engineer's local palace experience is unchanged**. MemAgora additions are strictly additive at the local level.

## Architectural Decisions

**Decided:**

- **Server framework — FastAPI** (v0.3). Python consistency with the rest of the codebase won over Go's single-binary story; the Docker image is the deployment unit either way. `agora/app.py` exposes `create_app(config=..., store=...)`, so the framework is confined to the HTTP edge.
- **Database — Postgres as the reference** (v0.3), behind the `AgoraStore` interface in `agora/storage/base.py`. SQLite ships alongside it, which is what keeps the abstraction honest: two real implementations pass the same conformance suite. Nothing above the storage layer knows which one is running.
- **Hook vs backend integration — the hook path won** (v0.2/v0.3). Classification runs from `mempalace classify` invoked by the save and precompact hooks, not from inside the backend wrapper. `backend_agora.py` still audits drawer writes, but the classifier never plugs into `_maybe_audit` as v0.1 anticipated: the hook has the transcript, and the backend only has chunks.
- **No built-in LLM pipeline — agent-driven emission** (v0.4). MemAgora does not manage its own LLM calls. The agent using the tools is the intelligence; it decides what is team-relevant and emits it via `memagora_record_fact` and `memagora_record_decision` MCP tools during the session. A separate classifier LLM call would redundantly re-process content the agent already understood and would require MemAgora to manage API keys and a second model dependency. The hook-based `mempalace classify` path (v0.2) remains as a fallback for sessions where the agent did not call emission tools, but agent-driven emission is the primary and intended path.

**Still open:**

- **Subsystem pruning** — Which remaining inherited subsystems to actively strip vs. leave dormant. AAAK, Layer 1 importance scoring, the substring-as-fuzzy-matching helper, and hardcoded dedup statistics were stripped in v0.1. See [FOUNDATION.md](./FOUNDATION.md) for the remaining audit.
- **Upstream backend stability** — MemAgora extends `mempalace/backends/base.py`; interface changes upstream could break it. See "Known Risks" below.

## MemAgora Project Structure (Target)

Two top-level code directories matching the two deployment units, plus a neutral `contracts/` for the shared HTTP schema:

    palace/                  # Engineer-side: foundation + MemAgora additions, all installed together
    ├── __init__.py
    ├── (audited, pruned MemPalace foundation per FOUNDATION.md —
    │    mcp_server.py, miner.py, searcher.py, backends/, …)
    ├── backend.py           # Wraps palace.backends.chroma with classifier integration
    ├── mcp_agora.py         # Agora MCP tools (emission + query) and wake-up team context
    ├── classifier.py        # Fallback hook-based classifier (agent-driven emission via MCP tools is primary)
    ├── client.py            # HTTP client posting to agora server
    ├── audit.py             # Local append-only audit log of team writes
    ├── config.py            # Endpoint, API key, classifier prompt (merged with inherited config at rename)
    └── core.py              # Was palace.py; renamed to avoid the palace/palace.py collision
    
    agora/                   # Deployable team server — built in v0.3, exists today
    ├── app.py               # create_app(config, store) — the FastAPI seam
    ├── main.py              # `agora-server` entrypoint
    ├── admin.py             # `agora-admin` — migrate, keys, export/import
    ├── auth.py              # API key mint/verify; key → deployment + engineer
    ├── models.py            # pydantic mirrors of contracts/ (parity-tested)
    ├── versioning.py        # Schema-version negotiation
    ├── api/                 # HTTP endpoints (facts, ingest, decisions, timeline, health)
    ├── storage/             # AgoraStore interface + postgres/sqlite + migrations
    ├── Dockerfile
    ├── docker-compose.yml
    └── pyproject.toml       # Own package — engineer-side installs don't pull FastAPI/Postgres
    
    contracts/               # Wire format shared between palace and agora
    ├── __init__.py
    ├── facts.py             # Fact payload schema
    ├── api.py               # Request/response shapes
    └── pyproject.toml       # Independently versioned; third-party clients can install just this
    
    hooks/                   # Claude Code hooks (env vars renamed at v1.0)
    docs/
    │   ├── architecture.md
    │   ├── deployment.md
    │   └── agent-integration.md   # How agents use the MCP emission and query tools
    tests/

**Why this layout:** every top-level directory maps to a deployable unit (`palace` ships to engineers, `agora` ships to teams) or a versioning boundary (`contracts` versions independently for rolling-deploy compatibility). "memagora" is the project / repo / CLI name — not a code directory. The inherited-vs-original boundary that earlier drafts captured as a directory split (`mempalace/` + `memagora/`) is captured instead by per-file provenance headers added during the v1.0 rename. That boundary is historical, not architectural.

**Today vs target:** the directory is currently `mempalace/`, not `palace/`. Rename is deliberately deferred to v1.0 to keep upstream cherry-picking cheap. Pre-rename, new MemAgora-specific code lands inside `mempalace/` directly (with `_agora` suffixes for files that collide with inherited names — those suffixes drop at the rename). See [ROADMAP.md](./ROADMAP.md) for full rename mechanics.

> **The audit of which inherited subsystems are on MemAgora's path versus dormant is in [FOUNDATION.md](./FOUNDATION.md).**

## Key Files for MemAgora Tasks

Paths below use the target post-rename structure. Until v1.0, substitute `mempalace/` for `palace/`.

- **Agora MCP tools**: `palace/mcp_agora.py` — emission (`memagora_record_fact`, `memagora_record_decision`) and query (`memagora_facts_about`, `memagora_timeline`, `memagora_decisions_about`, `memagora_why`), merged into `palace/mcp_server.py`'s registry. Also holds `team_context()`, which wake-up appends.
- **Classifier (fallback)**: `palace/classifier.py` — hook-invoked, for agents that do not call emission tools directly
- **Superseding a fact**: `memagora_record_fact(..., supersedes=...)` → `POST /ingest` `closes` → `AgoraStore.close_fact`. Facts are never deleted; closing sets `valid_to`.
- **Backend integration**: `palace/backend.py` — implements `palace/backends/base.py`, wraps `palace/backends/chroma.py`
- **HTTP client to agora**: `palace/client.py`
- **Local audit log**: `palace/audit.py`
- **Wire format / shared contracts**: `contracts/` — fact payload, API request/response shapes; imported by both palace and agora
- **Server endpoints**: `agora/api/` — one module per resource; add a router in `agora/app.py`
- **Storage abstraction**: `agora/storage/base.py` — subclass `AgoraStore`, register under the `agora.stores` entry-point group, and prove it by subclassing `agora.storage.testing.AbstractStoreContractSuite`
- **Agent guidance**: [docs/agent-integration.md](./docs/agent-integration.md) — what belongs in the agora, the prose boundary, and the `CLAUDE.md` snippet a deployment needs so its agents emit at all
- **System overview**: [docs/architecture.md](./docs/architecture.md)
- **Server auth**: `agora/auth.py` — the API key is the only source of `deployment_id` / `engineer_id`
- **Deployment config**: `agora/docker-compose.yml` and [docs/deployment.md](./docs/deployment.md)

For tasks involving the inherited MemPalace plumbing (mining, search, the local palace itself), refer to [FOUNDATION.md](./FOUNDATION.md).

## Known Risks

- **Upstream churn** — MemPalace is young and shipping fast. Backend interface changes between versions could break MemAgora. Mitigation: pin the upstream commit `mempalace-main` currently points to; advance only after manual testing of the `BaseBackend` / `BaseCollection` contract via the existing test suite. The `upstream` remote and `mempalace-main` branch already exist (see [ROADMAP.md](./ROADMAP.md) "Branch model"). The sync workflow:

      git fetch upstream
      git checkout mempalace-main
      git merge --ff-only upstream/main
      # Audit: git log master..mempalace-main, review each commit for path/contract impact
      # Selectively cherry-pick or merge into a feature branch off master, then PR into master

  We do not auto-track upstream. Every advance is gated on a contract review.
- **Agent emission discipline** — Agora quality depends on the agent actually calling the emission tools. An agent that never calls `memagora_record_decision` produces no rationale in the agora regardless of session depth. Each deployment's CLAUDE.md (or equivalent agent instructions) should explicitly encourage emission at decision and design points; [docs/agent-integration.md](./docs/agent-integration.md) has the snippet. Nothing in the system detects the absence, which is why it stays on this list.
- **Privacy expectations** — Engineers need confidence that the local-vs-team boundary is real. A single incident of raw content reaching the agora would damage trust. Agents must only emit structured facts and decisions via the emission tools — never raw conversation content.
- **Inherited brittleness** — Several known bugs and design issues exist in the MemPalace code MemAgora inherits. Most do not affect MemAgora directly because the affected subsystems aren't on MemAgora's path. See [FOUNDATION.md](./FOUNDATION.md) for the audit and the current strip/keep status of each subsystem.

## Working Notes for Coding Agents

- The codebase today contains substantial MemPalace code that MemAgora doesn't actively use. Treat it as legacy infrastructure being maintained for the parts MemAgora does use, not as authoritative reference.
- MemAgora-specific additions live in `palace/` (engineer-side), `agora/` (server), and `contracts/` (shared wire format). Pre-rename, engineer-side additions land inside `mempalace/` directly — see [ROADMAP.md](./ROADMAP.md) for the rename mechanics.
- When modifying inherited MemPalace files, check [FOUNDATION.md](./FOUNDATION.md) first to understand whether the file is on MemAgora's path or vestigial. Modifications to vestigial code are usually unnecessary.
- Bug fixes that improve genuinely-used MemPalace plumbing should be considered for upstream contribution back to MemPalace via the `upstream` git remote. Bug fixes to subsystems MemAgora has marked for removal are not worth contributing — strip them locally instead.
- MemAgora's novel logic — MCP emission tools, server endpoints, storage abstractions, contracts — stays in this repository and is not contributed upstream.

## Conventions

- **Python style**: snake_case for functions/variables, PascalCase for classes
- **Linter**: ruff with E/F/W rules
- **Formatter**: ruff format, double quotes
- **Commits**: conventional commits (`fix:`, `feat:`, `test:`, `docs:`, `ci:`)
- **Tests**: `tests/test_*.py`, fixtures in `tests/conftest.py`
- **Coverage**: 85% threshold (80% on Windows due to ChromaDB file lock cleanup)

## Setup

    pip install -e ".[dev]"

## Commands

    # Run tests
    python -m pytest tests/ -v --ignore=tests/benchmarks

    # Run tests with coverage
    python -m pytest tests/ -v --ignore=tests/benchmarks --cov=mempalace --cov-report=term-missing

    # Lint
    ruff check .

    # Format
    ruff format .

    # Format check (CI mode)
    ruff format --check .

### Agora server

The server has its own dependency profile — a plain `pip install -e ".[dev]"`
does not install FastAPI or psycopg, and the agora tests skip themselves when
it is absent.

    # Install the server alongside the palace
    pip install -e ".[dev]" ./contracts ./agora

    # Server suite (SQLite-backed; no Docker needed)
    python -m pytest tests/test_agora_*.py tests/test_client_http.py -v

    # Storage conformance against a real Postgres
    docker run -d --rm --name pg -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16
    AGORA_TEST_DSN=postgresql://postgres:pw@localhost:55432/postgres \
        python -m pytest tests/test_agora_storage.py -m postgres -v

    # Run it locally without Docker
    export AGORA_STORE=sqlite AGORA_SQLITE_PATH=./agora.sqlite3 AGORA_DEPLOYMENT_ID=dev
    agora-admin migrate && agora-admin issue-key --engineer "$USER"
    agora-server
