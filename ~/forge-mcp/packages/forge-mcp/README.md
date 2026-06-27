# forge-mcp

MCP server exposing the [forge guardrails library](https://github.com/antoinezambelli/forge) to Hermes Agent. Save structured-task workflows, run them against local LLMs (Ollama / llama-server / llamafile / vLLM) with forge's reliability layer (response validation, rescue parsing, retry nudges), and capture tool-call traces.

## Why

When a frontier model is doing novel work, send it to the frontier. When the task is **repeated and structured** — extract entities, classify sentiment, summarize in N bullets, translate, format as JSON — the local 8B/14B model with forge's guardrails is 70–90% as good for a fraction of the cost. This MCP lets you:

1. Define the workflow once (system prompt + tool schemas + terminal tool)
2. Save it to disk as JSON
3. Invoke it many times from any MCP-aware client (Claude Code, Hermes subagents, etc.)
4. Get back the model's structured answer + a per-turn trace for audit

## Tools

| Tool | Purpose |
|---|---|
| `forge_health` | Backend connectivity check, version, configuration |
| `forge_save_workflow` | Persist a workflow definition (system prompt + tools) |
| `forge_list_workflows` | List all saved workflows |
| `forge_get_workflow` | Fetch a saved workflow's full definition |
| `forge_delete_workflow` | Remove a saved workflow |
| `forge_run_workflow` | Execute a saved workflow against a user message |
| `forge_run_inline` | Execute a one-shot workflow (no save) |
| `forge_rescue_tool_calls` | Parse structured tool calls from non-canonical text |
| `forge_get_sampling_defaults` | Look up card-recommended sampling for a model |

## Configuration (env vars on MCP subprocess)

| Variable | Default | Purpose |
|---|---|---|
| `FORGE_BACKEND` | `ollama` | `ollama` \| `llamafile` \| `openai-compat` |
| `FORGE_BASE_URL` | per backend | Backend root URL |
| `FORGE_DEFAULT_MODEL` | unset | Default model to use when caller omits one |
| `FORGE_WORKFLOWS_DIR` | `~/.hermes/forge/workflows` | Where saved workflows live |
| `FORGE_TOOL_MODULES` | unset | Colon-separated module paths exporting `register_tools()` |

Pass these via `hermes mcp add --env KEY=VAL ...` at registration time.

## Example workflow

```python
{
  "name": "extract-companies",
  "description": "Pull company names and tickers out of a news blurb.",
  "system_prompt": "You are a precise entity extractor. Read the news blurb and respond with a JSON list of {\"name\": str, \"ticker\": str | null} objects via the respond tool. Tickers must be 1-5 uppercase letters; null if the company is not publicly traded.",
  "tools": [
    {
      "name": "respond",
      "description": "Submit your final structured answer.",
      "parameters": {
        "type": "object",
        "properties": {
          "answer": {"type": "string", "description": "JSON list of {name, ticker} objects"}
        },
        "required": ["answer"]
      }
    }
  ],
  "terminal_tool": "respond"
}
```

Save once, invoke many times:

```python
mcp__forge__forge_run_workflow(
  workflow_name="extract-companies",
  user_message="Tesla announced a 5-for-1 stock split. Apple also rallied after earnings."
)
```

## Install

```bash
cd C:/Data/Hermes/~/forge-mcp/packages/forge-mcp
pip install -e .
```

## Register with Hermes

```bash
hermes mcp add forge \
  --command forge-mcp \
  --env FORGE_BACKEND=ollama \
  --env FORGE_BASE_URL=http://localhost:11434 \
  --env FORGE_DEFAULT_MODEL=qwen3:8b
echo "Y" | hermes mcp add forge ...
hermes mcp test forge
```

## Limitations

- Workflows are stateless across calls (no built-in conversation memory). Pass `initial_messages` via a wrapper if you need multi-turn context.
- The schema-only tools (no `register_tools()` callable) return a placeholder message and nudge the model toward the terminal tool. For workflows that need real external tool execution, write a Python module exporting `register_tools()` and pass it via `FORGE_TOOL_MODULES`.
- forge is a Python library; this MCP is a thin wrapper. Forge's full power (SlotWorker, foreign-loop middleware, etc.) is not exposed — only the workflow runner.