---
name: qmd
description: Hybrid search over markdown knowledge bases via QMD MCP server. BM25 full-text + sqlite-vec embeddings + LLM reranking, on-device via node-llama-cpp with bundled GGUF models. Use when searching across the Obsidian vault, Hermes skill definitions, open-notebook exports, or any markdown KB on this machine. The MCP exposes 6 tools: qmd_search, qmd_vector_search, qmd_deep_search, qmd_get, qmd_multi_get, qmd_status.
platforms: [linux, macos, windows]
---

# QMD - Query Markup Documents

Local hybrid search engine for markdown knowledge bases. Indexes collections of `.md` files, then serves them through an MCP server with BM25 + vector + reranking pipelines.

## When to load this skill

- Searching the Obsidian vault for a topic, project, or note by content
- Finding Hermes skill definitions by topic (which skill handles X?)
- Retrieving past notes by docid (e.g. "get note `#abc123`")
- Building agent context for a task (bulk-fetch related notes via `qmd_multi_get`)
- Cross-collection search (one query across multiple KBs)

## MCP tools

| Tool | Backend | Cost profile | Use when |
|---|---|---|---|
| `qmd_search` | BM25 (FTS5) | <100ms, local only | Exact phrase, code symbol, error string |
| `qmd_vector_search` | sqlite-vec embeddings | ~1-2s first call (model load), then <500ms | Paraphrase / semantic similarity |
| `qmd_deep_search` | BM25 + vec + rerank + query expansion | ~3-8s (multi-step) | Best-effort recall for complex queries |
| `qmd_get` | direct | <50ms | Have a docid or path, need full content |
| `qmd_multi_get` | direct | <200ms for 10 files | Bulk fetch a folder/glob of notes |
| `qmd_status` | meta | <50ms | Index health, collection count, model state |

## Search-mode selection

Pick the cheapest tool that will work:

1. **Exact string / code symbol / error message** -> `qmd_search` (BM25 exact match)
2. **Concept / paraphrase / "how do I..."** -> `qmd_vector_search`
3. **Best-effort recall for an LLM agent's context window** -> `qmd_deep_search`
4. **Already have a docid or path from a prior search** -> `qmd_get`
5. **Need a folder of related notes (e.g. all Daily notes this week)** -> `qmd_multi_get`

## Output formats

All search tools accept `--json`, `--files`, `--md`, `--csv`, `--xml`:

- `--json` - structured output with snippets; best for programmatic parsing
- `--files` - just `docid,score,filepath,context` lines; best for batch retrieval
- `--md` - markdown format; best for human reading or LLM context

Use `--min-score 0.3` (or higher) to filter low-confidence hits. Use `-n 10` to cap result count.

## Collections on this machine

| Collection | Path | Contents |
|---|---|---|
| `skills` | `$HERMES_HOME/skills/` | 92 SKILL.md files describing every Hermes skill |
| `obsidian` | `$OBSIDIAN_VAULT_PATH` (default: `~/Documents/Obsidian Vault/`) | Bill's personal Obsidian vault |

## Adding a new collection

```sh
qmd collection add <path> --name <name> [--mask "**\*.md"]
qmd context add qmd://<name> "Description for LLM reranker context"
qmd embed
```

The context string is critical for `qmd_deep_search` reranking quality - it tells the LLM what kind of content the collection holds.

## Composition with other skills

- **`obsidian`** skill (note-taking) - writes/reads the vault; QMD searches it. Don't re-implement search in the obsidian skill.
- **`llm-wiki`** - builds a Karpathy-style interlinked markdown KB; QMD can index it as a collection.
- **`open-notebook`** - stores research notes in SurrealDB; export to markdown then add as a QMD collection.
- **`session_search`** - searches past session transcripts; different scope (conversations vs. curated KBs). Use session_search for "what did we do about X", use QMD for "what does my knowledge base say about X".

## MCP tool behavior in ollama mode

| Tool | Behavior |
|---|---|
| `qmd_search` | BM25 only, no model needed. Full quality. |
| `qmd_vector_search` | Uses ollama `/v1/embeddings` with bge-m3. ~270ms per query after first warmup. |
| `qmd_deep_search` | Full pipeline in ollama mode: query expansion via qwen3:8b, vector search via bge-m3, rerank via bge-m3 cosine similarity. ~10-20s typical. |
| `qmd_get` | Direct retrieval, no model needed. |
| `qmd_multi_get` | Bulk retrieval, no model needed. |
| `qmd_status` | Shows collections + index size, skips device/GPU section (avoids CUDA build noise). |

## Pitfalls

- **First `qmd embed` is slow.** With `QMD_USE_OLLAMA=1` set, each chunk needs an HTTP roundtrip to ollama (~270ms). 92 SKILL.md files = 243 chunks = ~70 seconds. Without ollama routing, `qmd embed` would download ~2GB of GGUF models.
- **`qmd_deep_search` rerank is cosine-similarity in ollama mode** (not a true cross-encoder). The rerank step computes bge-m3 cosine similarity between query and document embeddings rather than using a real reranker model. This works but adds modest improvement over `qmd_vsearch` because the signal is the same embedding model. A real cross-encoder would rank better, but `bge-reranker-v2-m3-GGUF` crashes ollama (`GGML_ASSERT(n_outputs_max)`) and `Qwen3-Reranker-4B` doesn't behave as a clean yes/no classifier.
- **`qmd status` skips the device/GPU section** when ollama mode is active (avoids the CUDA build error). Run `ollama ps` for live model status.
- **MCP server needs a restart to register.** After config.yaml changes, run `hermes mcp restart qmd` (or full gateway restart).
- **Indexer ignores files matching `.qmdignore`** (gitignore-style). Useful for excluding `Templates/`, `.obsidian/`, etc.
- **Vault path env var** - QMD doesn't read `OBSIDIAN_VAULT_PATH`; it reads the path you give `qmd collection add`. The env var is for the `obsidian` skill.

## Quick verification

```sh
# Status check
qmd status

# Smoke test the index
qmd search "obsidian vault" --json -n 3
qmd vector_search "how do I write a daily note" --json -n 3
qmd deep_search "skill authoring pattern" --json -n 3
```