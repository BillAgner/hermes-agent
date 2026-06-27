---
name: hermes-feature-implementation
description: "Implement a new Hermes feature end-to-end across CLI subcommand + dashboard API endpoint + SPA component + tests. Use when the user asks to add a `hermes foo` command, expose new data in the dashboard, wire a new endpoint at `/api/foo`, or make any change that touches the multi-layer Hermes codebase."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, feature, cli, dashboard, api, spa, refactor]
    related_skills: [plan, spike, systematic-debugging, test-driven-development, requesting-code-review]
---

# Hermes Feature Implementation

Implement a new Hermes feature end-to-end across four layers: CLI subcommand, dashboard API endpoint (FastAPI), SPA component (React/Vite), and pytest tests. Use this skill when the user asks to:

- "Add a `hermes foo bar` command"
- "Show me X on the dashboard"
- "Wire up a new endpoint at `/api/foo`"
- "Make the SPA call Y"

Hermes is a multi-layer system where each surface (CLI, dashboard, gateway, MCP) often shares an underlying implementation. Many features are 80% already built — only the surface is missing. **Discovery first** is the rule that saves the most time.

## When NOT to use this

- The change is purely internal (no user-facing surface) — read the affected files and edit directly.
- The change is config-only (`config.yaml`, `.env`) — no code work needed.
- The user wants a one-shot answer (research, analysis) — this skill is for code changes.
- A bundled skill already covers the territory (e.g. `qmd-install`, `hermes-agent-skill-authoring`).

## Core workflow

Five phases. Skip none.

### 1. Discovery first — before proposing anything

Read the codebase to find what already exists. Most of the work has been done at least once before. Concrete grep patterns per layer live in `references/discovery-first-checklist.md`.

Things to specifically check:
- **CLI verbs:** `hermes_cli/subcommands/<feature>.py` (argparse) + handler in `hermes_cli/<feature>.py` or `hermes_cli/main.py`
- **API endpoints:** `hermes_cli/web_server.py` — search for `/api/<feature>`
- **SPA API client:** `web/src/lib/api.ts` — search for `<feature>`
- **SPA pages/components:** `web/src/pages/<Feature>Page.tsx` or `web/src/components/<Feature>.tsx`
- **DB layer:** `hermes_state.py` — `SessionDB`, run history, etc.
- **MCP tools:** `mcp_servers/<feature>-mcp/`

**Concrete example:** when adding `hermes cron runs`, search for `cron.*runs`, `list_cron_job_runs`, `/api/cron/jobs/.*/runs` BEFORE writing code. If you find that `/api/cron/jobs/{id}/runs` already exists backed by `SessionDB.list_cron_job_runs`, you've saved half the work — the new CLI verb just calls the same DB function the dashboard uses.

### 2. Plan with the user — proposal, not implementation

After discovery, present a concrete inventory of what's missing vs. what exists, then offer concrete options. Lead with a table; Bill reads tables first.

```
## What's already wired up
- Endpoint /api/cron/jobs/<id>/runs exists (web_server.py line 7645)
- SessionDB.list_cron_job_runs exists (hermes_state.py line 2333)
- SPA has /cron page but doesn't call the runs endpoint

## What's missing
- CLI: no `cron show` / `cron runs` verb
- SPA: CronPage.tsx doesn't render run history

## Concrete options
| Option | Effort | What you get |
|---|---|---|
| A. CLI: `hermes cron show <id>` + `cron runs <id>` | ~1 hr | New CLI verbs reading the existing API |
| B. Dashboard cron panel with run history | ~2-3 hr | New SPA section reading the existing API |
```

Let the user pick. Do not start coding until they do.

### 3. Minimal change — extend, don't duplicate

Prefer, in order:
1. **Reuse existing code** — call the same DB function / helper the dashboard uses. Single source of truth.
2. **Extend existing files** — add a function to an existing module, not a new module.
3. **New file in an existing directory** — only when the existing files are too big or the concern is genuinely separate.

**Don't:**
- Add a `runs.jsonl` file when runs are already in SessionDB.
- Add a new API endpoint when one already exists.
- Add a new tool when one already does the job.
- Add a new component when an existing one can be extended.

### 4. Wire all four layers in coordination

Hermes has four user-facing surfaces that often need to stay in sync:

| Layer | Path | What to add |
|---|---|---|
| CLI | `hermes_cli/<feature>.py` + `hermes_cli/subcommands/<feature>.py` | handler function + argparse subparser |
| API | `hermes_cli/web_server.py` | `@app.get("/api/<feature>/...")` |
| SPA client | `web/src/lib/api.ts` | `fetchJSON<X>(...)` method + `interface X` |
| SPA page | `web/src/pages/<Feature>Page.tsx` (or inline section) | component using the API |
| Tests | `tests/hermes_cli/test_<feature>*.py` | argparse + behavior tests |

When the CLI and dashboard can share a backend (e.g. both calling `SessionDB.list_cron_job_runs`), they MUST — no parallel paths. If they can't share (e.g. CLI can't reach the dashboard server), have the CLI call the backend directly.

### 5. Test, build, verify E2E

After every change:

**Python tests** (see `references/windows-tooling-gotchas.md` if on Windows):
```bash
cd hermes-agent
./.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_<feature>.py -v --tb=short
```

**SPA build:**
```bash
cd web && npm run build
```

Verify the build landed by checking:
```bash
grep -oE 'index-[A-Za-z0-9]+\.js' hermes_cli/web_dist/index.html
grep -oE '<newFeatureSymbol>' hermes_cli/web_dist/assets/index-*.js
```

The bundle hash MUST change (e.g. `index-Bo5gTYJj.js` → `index-DoJEWo2s.js`). The new symbol MUST be present in the bundle.

**E2E against real data:**
```bash
./.venv/Scripts/python.exe hermes_cli/main.py <feature> show <real-id>
./.venv/Scripts/python.exe hermes_cli/main.py <feature> runs <real-id> --limit 5
```

Run against Bill's real install, not a fixture. If something looks wrong, fix it before declaring done.

## Pitfalls (cross-cutting)

### Tool gotchas (read before editing — details in references/windows-tooling-gotchas.md)

- **`patch` tool sometimes mangles indentation** on multi-line `old_string` replacements — especially in TS/JSX and Python files with nested structures. If `patch` succeeds but the diff shows weird indentation shifts, `git checkout HEAD -- <file>` and use a Python script via `terminal` instead.
- **`write_file` is destructive** — it overwrites the ENTIRE file. Never use it for targeted edits. Use `patch` for single-file edits, or write a small Python script to a `.py` file under `scripts/` and run it for multi-file bulk changes. Recover from mistakes with `git checkout HEAD -- <file>`.
- **`scripts/run_tests.sh` looks for `bin/activate` (POSIX)** — doesn't match Windows venvs which use `Scripts/activate`. On Windows, install pytest into the local venv (`uv pip install --python ./.venv/Scripts/python.exe pytest pytest-asyncio`) and run `pytest` directly with the hermetic env vars set manually.
- **`from X import Y` inside a function** — to patch Y in tests, patch `X.Y` (the source module), not `cron.Y` (the consumer). The local binding resolves to `X.Y` at call time, so `mock_patch.object(consumer, "Y")` fails with `AttributeError: does not have the attribute 'Y'`.

### Hermes-specific patterns (details in references/)

- **Cron run sessions are stored as `cron_<job_id>_<timestamp>`** in SessionDB with `source='cron'`. `list_cron_job_runs` does an id-range scan (`WHERE s.id >= ? AND s.id < ?`) ordered by `started_at DESC, id DESC`. When mocking, sort by `(started_at, id) DESC` to match — see `references/sessiondb-mocking-pattern.md`.
- **Job IDs can be looked up by name OR canonical id** via `cron.jobs.resolve_job_ref(ref)`. Returns `Optional[Dict]`; raises `AmbiguousJobReference` when multiple jobs share a name. Always handle both cases.
- **`hermes_cli/web_dist/` is the BUILT SPA**, not source. Source is `web/src/`. After `npm run build`, the new bundle lands here and the dashboard picks it up on next browser refresh — no server restart needed.
- **Profiles are isolated** — `HERMES_HOME` is profile-scoped. Code paths must use `get_hermes_home()` from `hermes_constants`, never `Path.home() / ".hermes"`. Tests must mock both `Path.home()` AND set `HERMES_HOME` env var.

### Style

- **Lead with the change, not a preamble.** Bill reads tables and code first.
- **Concrete options > vague hedging.** "I could do X, Y, or Z — here are the trade-offs" beats "let me think about how to approach this".
- **Single-file PowerShell scripts for admin ops**, not multi-step recipes. Idempotent, self-verifying with `[OK]/[FAIL]/[SKIP]` markers. Template in `references/admin-script-template.md`.

## References

- `references/discovery-first-checklist.md` — grep patterns per layer, what to look for
- `references/windows-tooling-gotchas.md` — patch indent, write_file destruction, run_tests.sh, hermetic env vars
- `references/sessiondb-mocking-pattern.md` — SessionDB ordering, AmbiguousJobReference, FakeDB template
- `references/admin-script-template.md` — PowerShell idempotent script template