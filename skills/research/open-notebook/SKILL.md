---
name: open-notebook
description: "Read and write Bill's curated open-notebook knowledge base. Use when the user asks about research notes, notebook contents, saved articles, prior findings, or wants to save findings back to a notebook. Backed by an MCP server with 12 tools exposed as on_* functions."
version: 0.1.0
author: Bill Agner
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, knowledge-base, notebook, curation, mcp]
    related_skills: [mcp-server-setup]
---

# open-notebook

Curated research workspace. Bill curates articles, videos, and notes in the
open-notebook UI (Docker container, REST API at `http://localhost:5055/api`).
This skill teaches the agent how to read those findings back as context and
write new findings into the notebook.

The MCP server is registered as `open_notebook` and exposes 12 tools (all
prefixed `on_` so they don't collide with `list_*` / `search` / `ask` from
other MCPs).

## When to use

Reach for these tools whenever the user says any of:

- "look at my notes on X", "what do I have on X", "check my notebook for X"
- "save this to the notebook", "make a note about Y"
- "search my knowledge base", "ask my notebook", "what does my research say about Z"
- "add this link to the notebook", "save this article"
- references a notebook by name (e.g. "the Hermes Project notebook")

If the user just wants a fresh web search, prefer `web_search` /
`last30days` instead — those hit the live web. open-notebook is for *Bill's*
curated material.

## The curate → share loop

```
Bill's UI                 open-notebook              Hermes agent
──────────                 ─────────────              ────────────
saves article       →      /api/sources       →       on_get_source(...)
                                              ↘
writes AI summary    →      /api/sources/.../insights  on_get_source_insights(...)
                                              ↘
drafts note         →      /api/notes         →       on_get_note(...)

Agent finds something useful while working ──→  on_create_note(...)
                                            ──→  on_create_source_link(...)
```

Read direction: UI → agent. Write direction: agent → UI (when findings are
useful).

## Tool reference

### Discovery (start here)

| Tool | When to call |
|------|-------------|
| `on_health` | First call in a session, or when tool calls start failing. Probes the API and returns `reachable`/`auth_enabled`. |
| `on_list_notebooks` | User mentions "my notebooks", "what notebooks do I have". |
| `on_list_sources(notebook_id, limit, offset)` | After identifying a notebook, see what's in it. |
| `on_list_notes(notebook_id, limit, offset)` | Same, for notes. Note list returns metadata only — call `on_get_note` for each id to pull full text. |

### Read (pull findings into context)

| Tool | When to call |
|------|-------------|
| `on_get_source(source_id)` | User wants the full content of an article/video/upload. Truncates `full_text` at 12K chars. |
| `on_get_note(note_id)` | User wants a specific note's full text. |
| `on_get_source_insights(source_id)` | **Start here** for AI-generated summaries of a source — usually more useful than raw `full_text`. |
| `on_search(query, type, limit, ...)` | Free-text or semantic search across sources + notes. `type="text"` (BM25) for keyword lookup, `type="vector"` for natural-language questions. |
| `on_ask(question, notebook_id?, model_id?)` | RAG Q&A against the knowledge base. **Note**: requires a default chat model to be configured in open-notebook Settings. If it fails with a model error, tell the user to set one in the UI. |

### Write (curate from the agent)

| Tool | When to call |
|------|-------------|
| `on_create_note(notebook_id, content, title?, note_type?)` | Persist findings the agent discovered into a notebook. `note_type="ai"` (default) marks it as agent-written; `"human"` treats it as Bill's. |
| `on_create_source_text(notebook_id, title, content, embed?)` | Drop raw text into the notebook (e.g. transcript, log dump). Set `embed=true` to enable vector search. |
| `on_create_source_link(notebook_id, url, title?, embed?, transformations?)` | Add a URL — open-notebook fetches + indexes it. Pass `transformations=["<id>"]` to run AI summaries at ingest time. |

## Workflow patterns

### Pattern 1 — "What do I have on X?"

```python
# 1. List notebooks to see what exists
on_list_notebooks()
# → e.g. "Hermes Project: notebook:nxej1m2gjgs65ot0haka"

# 2. Search across everything (BM25 first; only escalate to vector if hits are sparse)
on_search(query="Hermes context window", limit=10)

# 3. If hits are notes → on_get_note(note_id)
#    If hits are sources → on_get_source_insights(source_id) is usually the better read
```

### Pattern 2 — "Add this to my notebook"

```python
# 1. Confirm target notebook
on_list_notebooks()
# → notebook:abc123

# 2a. If it's a link the user pasted:
on_create_source_link(
    notebook_id="notebook:abc123",
    url="https://example.com/article",
    title="Article Title",
    embed=True,                              # optional: enable vector search
    transformations=["<dense-summary-id>"],   # optional: AI summary at ingest
)

# 2b. If it's findings the agent just discovered:
on_create_note(
    notebook_id="notebook:abc123",
    title="Hermes tool footprint audit 2026-06-18",
    content="## Findings\n- ...",
    note_type="ai",
)
```

### Pattern 3 — Curate → reuse

After completing a multi-step research task, if the user signals the
findings are durable ("save that", "put it in the notebook", "I'll need
that later"), default to writing a structured note rather than just
replying. Notes are searchable across future sessions; chat replies are not.

## Operational notes

- **Auth.** The default deployment has auth disabled (`OPEN_NOTEBOOK_AUTH`
  unset). If the user enables password protection, set
  `OPEN_NOTEBOOK_AUTH_TOKEN` in `~/.hermes/config.yaml` under
  `mcp_servers.open_notebook.env` to a bearer token — without it, every
  tool call returns 401/403.
- **Models.** `on_ask` requires a default chat model to be configured in
  open-notebook's Settings UI (Settings → API Keys → provider, then
  Models → set defaults). Without it the call fails — fall back to
  `on_search` + `on_get_source_insights` for retrieval-only.
- **Embedding.** Vector search (`on_search(type="vector")`) requires
  sources to have been embedded at ingest. If `minimum_score` rejects
  everything, check that the source's `embedded=true` (visible in
  `on_list_sources` output).
- **Pagination.** `on_list_notes` and `on_list_sources` paginate at 50 by
  default. For notebooks with more content, use `offset` to page through.
- **Async processing.** `on_create_source_link` may return before the
  source is fully processed (status="processing"). Poll with
  `on_get_source(id)` until `status="completed"` before reading insights.

## Files

- Source: `C:\Data\Hermes\~\open-notebook-mcp\packages\open-notebook-mcp\`
- Skill junction: `C:\Data\Hermes\skills\research\open-notebook` →
  `C:\Data\Hermes\~\open-notebook-mcp\skills\open-notebook`
- MCP binary: `C:\Data\Hermes\hermes-agent\venv\Scripts\open-notebook-mcp.exe`
- Hermes config: `mcp_servers.open_notebook` in `C:\Data\Hermes\config.yaml`
