# FOUNDATION.md

> This document describes **MemPalace** — the local-first AI memory system whose code MemAgora is built on top of — with explicit annotations about which parts MemAgora actively uses, which are dormant, and which are candidates for removal.
>
> For information about MemAgora itself, see [CLAUDE.md](./CLAUDE.md).
>
> **This is not a faithful reference to MemPalace as its authors describe it.** It is a reference to the parts of MemPalace's code that exist in this repo and how MemAgora relates to each of them. MemPalace's broader claims about retrieval accuracy, the "method of loci," and benchmark performance are not commitments MemAgora makes.

## What MemPalace Actually Provides

Stripped of marketing, MemPalace contributes two genuinely useful things to MemAgora:

**1. Agent interface plumbing.** The MCP server with working tools, Claude Code hooks for save and pre-compact lifecycle events, conversation format parsers for Claude/ChatGPT/Codex/Slack exports, ChromaDB integration with sensible defaults, and a CLI dispatcher. This is real engineering work — not algorithmically novel, but operationally necessary and time-consuming to rebuild.

**2. The wing/room/drawer organizational metaphor.** A human-comprehensible mental model for navigating memory. This does not improve retrieval accuracy — ChromaDB's defaults handle that on their own. It improves *navigability and scoping*. An agent can search within a specific wing rather than against all of memory, which is a relevance-and-context improvement, not an accuracy improvement.

Everything else MemPalace claims to provide (verbatim recall as a unique innovation, the AAAK compression dialect, the temporal knowledge graph, importance scoring, fuzzy matching) ranges from "real but oversold" to "broken and not worth keeping."

## Inherited Design Principles (and MemAgora's Position On Each)

MemPalace's authors articulate seven design principles. MemAgora's stance on each:

- **Verbatim always** — *Inherited.* Where MemAgora touches raw storage, the verbatim guarantee is preserved. MemAgora does not summarize or paraphrase user content stored locally.
- **Incremental only** — *Inherited.* Append-only ingest. MemAgora does not destroy palace data to rebuild.
- **Entity-first** — *Inherited where useful.* The wing/room organizational structure depends on entity identification. MemAgora uses this for the agora's structured fact storage as well.
- **Local-first, zero external API by default** — *Modified.* The local palace remains local-first. MemAgora introduces a single explicit, configured network call to a team agora server, but the call carries only classified, structured facts — never raw user content. The local palace's privacy guarantee is intact.
- **Performance budgets** — *Inherited.* Hooks under 500ms, startup under 100ms.
- **Privacy by architecture** — *Modified.* The palace remains private. The agora is shared by design — engineer sovereignty is preserved through the classifier boundary, not through architectural impossibility of network calls.
- **Background everything** — *Inherited.* No bookkeeping in the chat window.

Principles MemAgora does not inherit:

- The implication that MemPalace's wing/room/drawer architecture produces algorithmic improvement in retrieval accuracy. It does not. ChromaDB's defaults handle retrieval; the structure provides navigability.
- Claims of 100% recall as a design requirement. MemAgora does not measure itself against this standard.
- Framing rooted in the "method of loci" and Zettelkasten metaphors. These are MemPalace's framing devices, not MemAgora's commitments.

## Subsystem-by-Subsystem Status

The following table is the operational ground truth for MemAgora work. Before modifying any inherited file, check its status here.

### Actively Used by MemAgora

These subsystems are on MemAgora's critical path. Bug fixes here are worth contributing upstream.

| File / Subsystem | What It Does | Why MemAgora Uses It |
|---|---|---|
| `mempalace/mcp_server.py` | MCP server exposing tools to Claude Code | Required interface for agent integration |
| `mempalace/cli.py` | CLI dispatcher | Required for engineer setup and admin |
| `mempalace/config.py` | Configuration and input validation | Used for sanitization in MemAgora classifier inputs |
| `mempalace/miner.py` | Project file miner | Engineers ingest project files via this |
| `mempalace/convo_miner.py` | Conversation transcript miner | Engineers ingest Claude/ChatGPT/Codex/Slack exports via this |
| `mempalace/searcher.py` | Hybrid BM25 + vector search | Used for in-palace retrieval |
| `mempalace/palace.py` | Core palace operations | Foundation for all storage interactions |
| `mempalace/backends/base.py` | Pluggable backend interface | **MemAgora's primary integration point.** MemAgora's backend implementation extends this. |
| `mempalace/backends/chroma.py` | ChromaDB implementation | Default storage layer that MemAgora's backend wraps |
| `mempalace/normalize.py` | Transcript format detection | Required for multi-format ingest |
| `mempalace/entity_detector.py` | Auto-detect people/projects | Used for wing/room assignment |
| `mempalace/entity_registry.py` | Entity storage and disambiguation | Same |
| `mempalace/palace_graph.py` | Room traversal and cross-wing tunnels | Used for navigability — but see the `_fuzzy_match` note below |
| `hooks/mempal_save_hook.sh` | Stop hook | Triggers MemAgora's classifier on save |
| `hooks/mempal_precompact_hook.sh` | PreCompact hook | Triggers final save before context compression |

### Dormant — Present But Not Used

These subsystems exist in the inherited code but MemAgora doesn't invoke them. They're not actively harmful, but they're not earning their keep either.

| File / Subsystem | Status | Notes |
|---|---|---|
| `mempalace/dialect.py` (AAAK) | **Strip candidate.** | Demonstrated 12.4% accuracy regression. Token math was wrong at launch and uses approximate (not real-tokenizer) counts. Not load-bearing for anything MemAgora does. Likely first subsystem to be removed. |
| `mempalace/layers.py` Layer 1 importance scoring | **Strip candidate.** | Sorts by `importance` metadata that the mining pipeline never sets. Default value of `3` for every drawer makes the "Essential Story" effectively random order. Broken in MemPalace, not used by MemAgora. |
| `mempalace/layers.py` token estimation | **Bug to fix or strip.** | Two different broken methods (`len(text)//4` in one place, `len(words)*1.3` in another). Neither uses a real tokenizer. README claims "~170 tokens" for wake-up but actual counts are 600-900. Fix only if a use case actually depends on accurate counts. |
| `mempalace/dedup.py` | **Use with caveat.** | The dedup operation itself works. The reported statistics (`int(len(ids) * 0.4)`) are hardcoded estimates, not computed values. The numbers shown to users are misleading; the dedup behavior is fine. |
| `mempalace/spellcheck.py` | **Dormant.** | Auto-corrects user messages. Not used by MemAgora. |
| `mempalace/exporter.py` | **Dormant.** | Palace data export. Not used by MemAgora's flow but useful for engineers. Keep. |
| `mempalace/onboarding.py` | **Selectively used.** | Interactive first-run setup. MemAgora may extend this with its own prompts but the underlying subsystem stays. |
| `mempalace/repair.py` | **Dormant but useful.** | Palace repair and consistency checks. Engineers may invoke this manually. Keep. |
| `mempalace/migrate.py` | **Use with caution.** | Handles ChromaDB version migrations. Important when upgrading the underlying vector store. Keep but understand it before invoking. |
| `mempalace/split_mega_files.py` | **Dormant.** | Edge case for split transcript files. Keep dormant. |
| `mempalace/hooks_cli.py` | **Used.** | Hook management CLI. MemAgora extends this for its own hook configuration. |
| `mempalace/query_sanitizer.py` | **Used.** | Prompt contamination prevention. MemAgora applies this to classifier inputs. |

### Known Issues With Misleading Naming

Some inherited code has names that suggest more sophistication than the implementation provides. Worth knowing before relying on them:

- **`mempalace/palace_graph.py:_fuzzy_match`** — Despite the name, this is Python's `in` operator doing exact substring containment. No Levenshtein, no trigram similarity, no actual fuzzy matching algorithm. Treat it as substring matching and don't expect more.
- **"Knowledge graph" in `mempalace/knowledge_graph.py`** — A SQLite database with two tables (entities and triples) implementing a standard SCD Type 2 pattern from data warehousing. Not graph-theoretic, no graph algorithms. Useful for what it does (temporal entity-relationship storage) but don't expect more.

## Inherited Architecture Diagram

This is what MemPalace's architecture looks like as inherited. MemAgora layers on top without modifying this:

    User → CLI / MCP Server → Storage Backend (ChromaDB default, pluggable)
                            → SQLite (knowledge graph)

    Palace structure:
      WING (person/project)
        └── ROOM (day/topic)
              └── DRAWER (verbatim text chunk)

    Index layer (AAAK) — currently dormant, strip candidate

    Knowledge Graph:
      ENTITY → PREDICATE → ENTITY (with valid_from / valid_to dates)

## Key Files for Inherited-Layer Tasks

When working on the parts MemAgora actively uses:

- **Adding an MCP tool**: `mempalace/mcp_server.py` — add handler function + TOOLS dict entry
- **Changing search**: `mempalace/searcher.py`
- **Modifying mining**: `mempalace/miner.py` (project files) or `mempalace/convo_miner.py` (transcripts)
- **Adding a storage backend**: subclass `mempalace/backends/base.py`, register in `backends/__init__.py`
- **Input validation**: `mempalace/config.py` — `sanitize_name()` / `sanitize_content()`
- **Tests**: mirror source structure in `tests/test_<module>.py`
