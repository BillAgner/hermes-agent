# Discovery-first checklist

Concrete grep patterns + what to look for at each Hermes layer before you propose building anything. Run these first, present what you find to the user, then they pick the scope.

## CLI

```bash
# Subcommand dispatch (argparse)
rg -n "cron_command|kanban_command|webhook_command" hermes_cli/subcommands/

# Handler implementations
rg -n "^def cron_|^def kanban_|^def webhook_" hermes_cli/

# Dispatch wiring in main.py
rg -n "def cmd_cron|def cmd_kanban" hermes_cli/main.py

# Test coverage of existing verbs
ls tests/hermes_cli/test_<feature>*.py
```

Look for: subparser definitions, `add_parser` calls, `func=cmd_<feature>` wiring. If `<feature>` already has `cmd_<feature>` and a subparser, the verb exists; only the surface might be missing.

## API (FastAPI dashboard)

```bash
# Existing endpoints
rg -n "@app\.(get|post|put|delete).*<feature>" hermes_cli/web_server.py
rg -n "@app\.(get|post|put|delete).*/api/<feature>" hermes_cli/web_server.py

# Endpoint helpers
rg -n "def _<feature>_|async def <feature>" hermes_cli/web_server.py

# Test coverage
ls tests/hermes_cli/test_web_server_<feature>*.py
```

Look for: route decorators, `_call_cron_for_profile` style helpers, `SessionDB` calls. If an endpoint already returns the data you want, you don't need a new one — just consume it from the new layer (CLI or SPA).

## SPA — API client

```bash
# Existing API methods
rg -n "get<Feature>|<feature>:" web/src/lib/api.ts

# Type definitions
rg -n "interface <Feature>|type <Feature>" web/src/lib/api.ts
```

Look for: `fetchJSON<X>(...)` methods, `interface X` exports. If `get<Feature>` already exists, the SPA has the data; only the component might be missing.

## SPA — pages and components

```bash
# Existing pages
ls web/src/pages/<Feature>Page.tsx 2>/dev/null
ls web/src/components/<Feature>*.tsx 2>/dev/null

# Plugin slot hooks (where new sections can inject without rebuilding)
rg -n "PluginSlot|<feature>:" web/src/pages/<Feature>Page.tsx

# Other places that might consume the data
rg -rn "<feature>|\"feature\"" web/src/
```

Look for: page component, modal/dialog components, plugin slots. If the page exists but a section is missing, edit the page directly. If the page doesn't exist, decide whether to create it or extend an existing page with a `PluginSlot`.

## DB layer (SessionDB)

```bash
# Existing queries
rg -n "def list_<feature>|def get_<feature>" hermes_state.py

# Test fixtures
ls tests/hermes_state/test_<feature>*.py
```

Look for: query methods that return the data you need. `SessionDB.list_cron_job_runs` is a good example of an existing index-range scan method that handles the work for both CLI and dashboard.

## MCP tools

```bash
# MCP server binary
ls ~/.hermes/hermes-agent/venv/Scripts/<feature>-mcp.exe 2>/dev/null
rg -n "<feature>_mcp|mcp__<feature>" hermes_cli/config.yaml

# Tool definitions
ls ~/.hermes/~\~\<feature>-mcp/ 2>/dev/null
ls mcp_servers/<feature>-mcp/ 2>/dev/null
```

Look for: registered MCP server, tool definitions, FastMCP decorators. If an MCP server already exposes the data via tools, you can wire it into the dashboard as a plugin instead of writing a new endpoint.

## Cron jobs (any feature that interacts with cron)

```bash
# Job store
rg -n "JOBS_FILE|cron/" cron/jobs.py

# Scheduler hooks
rg -n "def run_job|def tick" cron/scheduler.py

# Tool surface for agents
ls tools/cronjob*.py
```

Look for: `~/.hermes/cron/jobs.json` schema, scheduler hook points (`run_job`, `tick`), and the `cronjob` tool that agents use to schedule jobs.

## Telemetry and observability

```bash
# Usage sidecar
ls ~/.hermes/skills/.usage.json 2>/dev/null
rg -n "skill_usage" tools/

# Logs
ls ~/.hermes/logs/
```

Look for: existing usage tracking that may already cover your feature.

## What to look for in the result

After grepping, summarize:

```
## What's already wired up
- Endpoint /api/<feature>/<thing> exists (web_server.py line NNNN)
- SessionDB.list_<feature> exists (hermes_state.py line NNNN)
- SPA has /<feature> page but doesn't render <thing>
- Agent has `cronjob` tool already, no surface changes needed

## What's missing
- CLI: no `<feature> <verb>` subcommand
- SPA: <Feature>Page.tsx doesn't call the runs endpoint
- Tests: no coverage for the <verb> dispatch path

## Concrete options
| Option | Effort | What you get |
|---|---|---|
| A. CLI verb only | ~1 hr | ... |
| B. SPA section only | ~2 hr | ... |
| A+B | ~3 hr | ... |
```

Then wait for the user to pick before writing any code.