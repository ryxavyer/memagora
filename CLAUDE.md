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

**What MemAgora does not inherit, and will likely strip:**

- **AAAK compression dialect** — Demonstrated 12.4% accuracy regression for compression that doesn't reliably save tokens at the scales engineers actually operate at. Not load-bearing for MemAgora. Candidate for removal.
- **Layer 1 "Essential Story" importance scoring** — Sorts by metadata that the mining pipeline never sets, making the output effectively random order. Broken in MemPalace, not used by MemAgora. Candidate for removal.
- **Substring-as-fuzzy-matching** — `palace_graph.py:_fuzzy_match` is `in` operator wrapped in misleading naming. Not used by MemAgora.
- **Hardcoded dedup statistics** — `dedup.py` estimates duplicates as `int(len(ids) * 0.4)` rather than computing them. Cosmetic in MemPalace, not used by MemAgora.
- **MemPalace's marketing claims** — The 96.6% LongMemEval score is largely ChromaDB's default behavior. The "memory palace architecture" is metadata strings on ChromaDB documents. MemAgora's value proposition is institutional memory, not benchmark performance, so none of this matters here.

**Trajectory:** Over time, MemPalace's footprint in MemAgora is expected to shrink. The plumbing stays. The organizational metaphor stays. Layers that don't earn their keep get stripped or replaced as MemAgora matures. The repo today inherits more of MemPalace than it will a year from now, and that's intentional.

> **For details on the MemPalace foundation as it currently exists in this repo — including which subsystems MemAgora actively uses, which it ignores, and which are candidates for removal — see [FOUNDATION.md](./FOUNDATION.md).**

## Mental Model

- Palace — your private memory. Verbatim, local, sovereign. Your raw stream of consciousness.
- Agora — the public square. Structured facts extracted from individual palaces and shared across the team. Curated, not raw.

The name comes from the Greek agora — the public square where citizens chose to bring ideas for collective benefit. The word "chose" matters. The classifier respects engineer sovereignty by gating what propagates from palace to agora. Raw verbatim chunks stay private. Only structured, team-relevant facts cross the boundary.

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

MemAgora is designed as a **deployable template** — the same codebase, deployed independently per team. Three layers, all swappable:

    Engineer's local palace (MemPalace plumbing, MemAgora-curated)
           │
           ▼
    MemAgora Classifier ──── classifier prompt configurable per deployment
           │
           ▼
    MemAgora Backend ─────── HTTP client posting to a configured endpoint
           │
           ▼
    Team Agora Server ────── pluggable storage layer (Postgres default,
                             deployable swap for other backends)

The integration with MemPalace happens through MemPalace's pluggable backend interface (`mempalace/backends/base.py`). MemAgora ships a backend implementation that wraps the default ChromaDB backend, performs the normal local write, and additionally invokes the classifier on team-relevant content. Classified facts are POSTed to the configured agora server.

The agora server itself is a separate deployable unit. Each team stands up their own instance. The server's storage layer is also abstracted — Postgres is the default and reference implementation, but a team should be able to swap in MySQL, a different KG store, or whatever fits their existing infrastructure.

The core invariant: **regardless of deployment configuration, the engineer's local palace experience is unchanged**. MemAgora additions are strictly additive at the local level.

## Open Architectural Decisions

These are deliberately unresolved as of this writing. This document should be updated as they're decided:

- **Server framework** — FastAPI is the leading candidate (Python consistency with MemPalace, lightweight, async). Go is under consideration for single-binary deployment simplicity. No commitment yet.
- **Database** — Postgres is the leading candidate for the reference implementation. The pluggable layer should not assume Postgres specifically.
- **Hook vs backend integration** — Backend extension via `mempalace/backends/base.py` is the leading approach. The PreCompact hook may still need lightweight handling separately. Backend stability across MemPalace versions is a known risk; see "Known Risks" below.
- **Subsystem pruning** — Which MemPalace subsystems to actively strip vs. leave dormant. AAAK and Layer 1 importance scoring are likely candidates for removal as the codebase matures, but not on day one. See [FOUNDATION.md](./FOUNDATION.md) for the current state.

## MemAgora Project Structure (Target)

Two top-level code directories matching the two deployment units, plus a neutral `contracts/` for the shared HTTP schema:

    palace/                  # Engineer-side: foundation + MemAgora additions, all installed together
    ├── __init__.py
    ├── (audited, pruned MemPalace foundation per FOUNDATION.md —
    │    mcp_server.py, miner.py, searcher.py, backends/, …)
    ├── backend.py           # Wraps palace.backends.chroma with classifier integration
    ├── classifier.py        # Local-vs-team classification
    ├── client.py            # HTTP client posting to agora server
    ├── audit.py             # Local append-only audit log of team writes
    ├── config.py            # Endpoint, API key, classifier prompt (merged with inherited config at rename)
    └── core.py              # Was palace.py; renamed to avoid the palace/palace.py collision
    
    agora/                   # Deployable team server (separate dependency profile)
    ├── api/                 # HTTP endpoints
    ├── models/              # Knowledge graph schema
    ├── storage/             # Pluggable storage backends (Postgres reference)
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
    │   └── classifier-tuning.md
    tests/

**Why this layout:** every top-level directory maps to a deployable unit (`palace` ships to engineers, `agora` ships to teams) or a versioning boundary (`contracts` versions independently for rolling-deploy compatibility). "memagora" is the project / repo / CLI name — not a code directory. The inherited-vs-original boundary that earlier drafts captured as a directory split (`mempalace/` + `memagora/`) is captured instead by per-file provenance headers added during the v1.0 rename. That boundary is historical, not architectural.

**Today vs target:** the directory is currently `mempalace/`, not `palace/`. Rename is deliberately deferred to v1.0 to keep upstream cherry-picking cheap. Pre-rename, new MemAgora-specific code lands inside `mempalace/` directly (with `_agora` suffixes for files that collide with inherited names — those suffixes drop at the rename). See [ROADMAP.md](./ROADMAP.md) for full rename mechanics.

> **The audit of which inherited subsystems are on MemAgora's path versus dormant is in [FOUNDATION.md](./FOUNDATION.md).**

## Key Files for MemAgora Tasks

Paths below use the target post-rename structure. Until v1.0, substitute `mempalace/` for `palace/`.

- **Classifier logic**: `palace/classifier.py` — prompt and post-processing
- **Backend integration**: `palace/backend.py` — implements `palace/backends/base.py`, wraps `palace/backends/chroma.py`
- **HTTP client to agora**: `palace/client.py`
- **Local audit log**: `palace/audit.py`
- **Wire format / shared contracts**: `contracts/` — fact payload, API request/response shapes; imported by both palace and agora
- **Server endpoints**: `agora/api/`
- **Storage abstraction**: `agora/storage/` — implement new backend by subclassing the abstract storage interface
- **Deployment config**: `agora/docker-compose.yml` — reference deployment

For tasks involving the inherited MemPalace plumbing (mining, search, the local palace itself), refer to [FOUNDATION.md](./FOUNDATION.md).

## Known Risks

- **Upstream churn** — MemPalace is young and shipping fast. Backend interface changes between versions could break MemAgora. Mitigation: pin MemPalace version, test before bumping, maintain a `mempalace-master` branch for tracking upstream cleanly.
- **Classifier quality** — The classifier prompt is the core novel piece of MemAgora and is inherently context-dependent. A poorly-tuned classifier either propagates noise or starves the agora. Each deployment will need to iterate on its prompt against real usage.
- **Privacy expectations** — Engineers need confidence that the local-vs-team boundary is real. A single incident of raw content reaching the agora would damage trust. The classifier's default behavior must be conservative — when uncertain, content stays local.
- **Inherited brittleness** — Several known bugs and design issues exist in the MemPalace code MemAgora inherits. Most do not affect MemAgora directly because the affected subsystems aren't on MemAgora's path. See [FOUNDATION.md](./FOUNDATION.md) for the audit and the current strip/keep status of each subsystem.

## Working Notes for Coding Agents

- The codebase today contains substantial MemPalace code that MemAgora doesn't actively use. Treat it as legacy infrastructure being maintained for the parts MemAgora does use, not as authoritative reference.
- MemAgora-specific additions live in `palace/` (engineer-side), `agora/` (server), and `contracts/` (shared wire format). Pre-rename, engineer-side additions land inside `mempalace/` directly — see [ROADMAP.md](./ROADMAP.md) for the rename mechanics.
- When modifying inherited MemPalace files, check [FOUNDATION.md](./FOUNDATION.md) first to understand whether the file is on MemAgora's path or vestigial. Modifications to vestigial code are usually unnecessary.
- Bug fixes that improve genuinely-used MemPalace plumbing should be considered for upstream contribution back to MemPalace via the `upstream` git remote. Bug fixes to subsystems MemAgora has marked for removal are not worth contributing — strip them locally instead.
- MemAgora's novel logic — classifier prompts, server endpoints, storage abstractions — stays in this repository and is not contributed upstream.

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
