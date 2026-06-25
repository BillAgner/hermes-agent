# open-notebook-mcp

MCP server that exposes the [open-notebook](https://github.com/lfnovo/open-notebook)
REST API as a curated research workspace for Hermes Agent.

The intended workflow:

1. You curate in the open-notebook UI (add sources, write notes, generate insights).
2. From any Hermes session, you ask the agent to read what you've curated.
3. The agent pulls notes/sources/search hits back into its context.
4. The agent can write new notes or sources back into the notebook when findings are useful.

## Tools (11 total)

| Tool | Purpose |
|------|---------|
| `on_list_notebooks` | List all notebooks (archived filter, ordering) |
| `on_list_sources` | List sources in a notebook |
| `on_list_notes` | List notes in a notebook |
| `on_get_source` | Fetch full source content + topics + insights_count |
| `on_get_note` | Fetch a single note's full content |
| `on_get_source_insights` | Fetch AI-generated insights for a source |
| `on_search` | Text or vector search across sources + notes |
| `on_ask` | Ask a question against the knowledge base (AI Q&A) |
| `on_create_note` | Write a new note into a notebook |
| `on_create_source_text` | Add a text source (paste in raw content) |
| `on_create_source_link` | Add a link source (URL — open-notebook fetches + embeds) |

The `on_` prefix keeps names short, distinctive, and prevents collisions with
`list_*` / `search` / `ask` verbs used by other MCPs.

## Configuration

| Env var | Default | Notes |
|---------|---------|-------|
| `OPEN_NOTEBOOK_URL` | `http://localhost:5055` | Base URL of the open-notebook API. API path `/api` is appended automatically. |
| `OPEN_NOTEBOOK_AUTH_TOKEN` | (unset) | Optional bearer token if your deployment enables `OPEN_NOTEBOOK_AUTH`. |
| `OPEN_NOTEBOOK_TIMEOUT` | `30.0` | Per-request timeout in seconds. |

## Install

```bash
cd ~/open-notebook-mcp/packages/open-notebook-mcp
uv pip install -e .
```

Then register with Hermes:

```bash
hermes mcp add open_notebook \
  --command "C:/Data/Hermes/hermes-agent/venv/Scripts/open-notebook-mcp.exe" \
  --args "" <<< "Y"
```

(adjust path if your venv lives elsewhere)
