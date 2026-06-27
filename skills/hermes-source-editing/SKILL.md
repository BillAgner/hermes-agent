---
name: hermes-source-editing
description: "Edit Python or TypeScript source in the Hermes codebase on Windows without losing data, mangling indentation, or building a stale SPA bundle. Use when patching hermes_cli/*.py, modifying web/src/*.tsx, adding tools, writing CLI subcommands, or making any code change that will be picked up by `hermes` / the dashboard / the gateway. Covers the patch tool's indentation quirk, the write_file destruction trap, the npm run build cycle, and the Windows test runner bypass. Load BEFORE making more than a one-line edit to the repo."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [hermes, development, editing, patch, build, spa, windows, git]
    related_skills: [hermes-agent, hermes-agent-skill-authoring]
---

# Hermes Source Editing

You are about to edit source files inside the Hermes repo (or a sibling
checkout). Hermes has a Python backend (`hermes_cli/*.py`, `cron/*.py`,
`tools/*.py`, `gateway/*.py`, ...) and a React/Vite SPA (`web/src/**`).
Both are loaded by the runtime, so a careless edit on either side can:

- Indent a block wrong and break every parser that imports it
- Destroy a 2000-line file because `write_file` overwrites unconditionally
- Ship a SPA change that the browser never sees because the bundle wasn't rebuilt

This skill encodes the rules that keep those things from happening. Load it
before any non-trivial edit. After three or four clean edits in a row you
can stop re-loading.

## When to Use

Any of these should trigger loading this skill first:

- Editing Python under `hermes_cli/`, `cron/`, `tools/`, `agent/`, `gateway/`, `web_server.py`, or any `run_agent.py`-adjacent module
- Editing TypeScript/TSX under `web/src/` (the dashboard SPA)
- Adding a new CLI verb, slash command, tool, or cron-related feature
- Debugging a bug that might require touching both Python and the SPA (e.g. a dashboard widget)
- About to write more than one line to a file that already exists

## Core Workflow

```
1. READ the file you are about to edit (read_file, not cat).
2. PICK the right edit tool:
   - patch:  surgical, multi-file impossible. Indentation-sensitive — see pitfall #1.
   - write_file: ENTIRE FILE overwrite. Only use for new files or full rewrites.
                 Always re-read first to preserve untouched sections.
3. IF you touched web/src/*.tsx → cd web && npm run build (pitfall #3).
4. RUN tests: scripts/run_tests.sh on POSIX, ./.venv/Scripts/python.exe -m pytest on Windows (pitfall #4).
5. HARD-REFRESH the browser if the SPA changed (Ctrl+Shift+R / Cmd+Shift+R).
```

## Quick Reference

| Tool | Use it for | Pitfall |
|------|-----------|---------|
| `patch` | one targeted change, one file | Indentation gets mangled on indented multi-line content — see references/patch-tool-quirks.md |
| `write_file` | new files, full rewrites | **Replaces the entire file**. If the file already exists and you pass only the new content, the rest is gone. `git checkout HEAD -- <path>` to recover. |
| `terminal + python -c` / heredoc | surgical edits on indented blocks when patch keeps failing | Reliable; no indentation shift. Use this when patch fails twice on the same block. |
| `git checkout HEAD -- <path>` | recovery from accidental write_file overwrite | Restores the file from the last commit. Use it the moment you realize write_file blew away content you meant to preserve. |
| `npm run build` (in web/) | rebuild SPA after any web/src/** change | Output goes to `../hermes_cli/web_dist/`. Bundle hash changes; old cache busts on hard refresh. |
| `.venv/Scripts/python.exe -m pytest` | run tests on Windows | Set `TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1` for CI-parity-ish behavior. |

## Pitfalls

### 1. `patch` tool mangling indentation

`patch` (mode='replace') with a multi-line `new_string` whose content sits
inside an indented block frequently inserts the new content shifted by 2
extra spaces, or with mixed indent levels. It then runs a syntax lint that
flags `IndentationError: unexpected indent` and refuses to claim the edit.

This was reproduced three times in one session on `hermes_cli/cron.py`,
`hermes_cli/subcommands/cron.py`, and `web/src/lib/api.ts`. The first two
attempts corrupted each file. Recovery: `git checkout HEAD -- <path>` and
re-attempt with a different tool (see workaround below).

**Workaround order of preference:**

1. **Python heredoc / script for the edit.** Save the patch as a small
   `scripts/_patch_<name>.py` file, run it via `python`, then `rm` it.
   Reliable because Python `str.replace` operates on the exact bytes you
   typed. The downside: one extra file.

2. **`terminal + python -c "..."`.** Inline Python with `assert old in
   content; content = content.replace(old, new, 1)` is the same pattern
   without the script file. Risk: bash heredocs containing escape
   sequences can time out under tool-watch heuristics, so keep it short or
   write to a file first.

3. **Multiple smaller `patch` calls.** Each call edits a few lines whose
   indentation matches what the tool expects. More brittle, but no
   auxiliary files.

**Never:** keep retrying the same multi-line `patch` and hoping it works.
Two failures on the same block means switch tools.

### 2. `write_file` is destructive on existing files

`write_file(path, content)` ALWAYS overwrites the entire file. The
description says this; in practice it's easy to forget when you've been
writing patches for an hour and switch to a full rewrite. Calling it with
the new content of a 2280-line file you intended only to edit one block
of destroys the file. The error message says "bytes_written: N" — that N
is the new file size, not the diff. You won't get a warning.

**Recovery:** `git checkout HEAD -- <path>` restores the file from the
last commit. No commit yet? You're in trouble — this skill assumes the
repo is committed at session start.

**Rule:** before `write_file` on an existing file, re-read the file (full
content, not just the section you're changing) and pass the entire
intended end state. If your new content is shorter than 500 lines and the
file is longer, you're almost certainly making a mistake.

### 3. SPA rebuild cycle

The dashboard SPA source lives in `web/src/` (React + Vite). The runtime
serves the BUILT bundle from `hermes_cli/web_dist/`. Editing `web/src/*`
without rebuilding leaves the bundle unchanged — your edit is invisible
until you rebuild and the browser picks up the new bundle.

**Edit-to-live cycle:**

```bash
# 1. Edit web/src/pages/CronPage.tsx (or wherever)
# 2. Build:
cd web && npm run build
# 3. Verify the bundle hash changed:
grep -oE 'index-[A-Za-z0-9]+\.js' ../hermes_cli/web_dist/index.html
# 4. The user hard-refreshes (Ctrl+Shift+R / Cmd+Shift+R) to bypass cache.
```

Without step 3 you'll deploy "the change that wasn't" — the build either
failed silently (TS error in your file) or the cache served the old
bundle. Both look identical from the browser side. The bundle hash check
catches the first case; the hard refresh catches the second.

The new bundle filename contains a content hash, so `index-Bo5gTYJj.js`
becomes `index-DoJEWo2s.js` after a build that changed any byte. If the
hash doesn't change, your edit didn't make it in.

### 4. `scripts/run_tests.sh` does not work on Windows

It checks for POSIX venv layouts (`$REPO_ROOT/.venv/bin/activate` /
`$REPO_ROOT/venv/bin/activate`). Windows venvs live under `Scripts/` not
`bin/`. The Hermes-installed venv at `venv/Scripts/` also lacks `pip`
and `pytest` (stripped for end-user install size).

**Workaround:**

```bash
# One-time: install pytest into your .venv
uv pip install --python ./.venv/Scripts/python.exe pytest pytest-asyncio

# Per-run: invoke pytest with hermetic env vars manually
PYTHONPATH=. TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 \
PYTHONDONTWRITEBYTECODE=1 ./.venv/Scripts/python.exe -m pytest \
  tests/hermes_cli/test_foo.py -v --tb=short
```

The full content of the bypass recipe — and a more thorough version
covering end-user installs where there's no `.venv` at all — lives in
the protected `hermes-agent` skill's "Windows-Specific Quirks" section.
This skill just wants to remind you: **don't expect
`./scripts/run_tests.sh` to work on Windows. Use `.venv/Scripts/python`
+ `pytest` directly.**

### 5. Dashboard server reads static files on disk — no restart needed

The dashboard at `http://127.0.0.1:9119` serves files from
`hermes_cli/web_dist/`. After `npm run build` the new bundle is served on
the next request. **Do not** `hermes restart` or kill the dashboard
process just to deploy an SPA edit — it will pick up automatically on
browser refresh. This matters because restarting the dashboard
interrupts in-flight sessions (your memory note: "Avoid restarting the
Hermes gateway / agent process during long-running autonomous work").

### 6. Verify the dashboard actually picked up the new bundle

After a SPA rebuild, the user's browser may be caching the old bundle
aggressively. The new bundle filename includes a content hash, so:

```bash
# Server-side: confirm what's being served
curl -sS http://127.0.0.1:9119/ | grep -oE 'index-[A-Za-z0-9]+\.js'
```

If this hash matches what `index.html` references, the server is ready.
The browser still needs a hard refresh. If the user reports "the new
feature isn't showing up", their cache is the first thing to suspect.

## How to Run

For Python-only edits:

```bash
# Syntax check before tests
./.venv/Scripts/python.exe -c "import ast; ast.parse(open('hermes_cli/cron.py').read()); print('OK')"

# Run targeted tests
PYTHONPATH=. TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 \
PYTHONDONTWRITEBYTECODE=1 ./.venv/Scripts/python.exe -m pytest \
  tests/hermes_cli/test_cron.py -v --tb=short
```

For SPA edits:

```bash
cd web && npm run build
# Confirm bundle hash changed
grep -oE 'index-[A-Za-z0-9]+\.js' ../hermes_cli/web_dist/index.html
```

For full coverage before pushing:

```bash
PYTHONPATH=. TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 \
PYTHONDONTWRITEBYTECODE=1 ./.venv/Scripts/python.exe -m pytest tests/ -q --tb=short
```

## Verification

A "successful" edit to Hermes source leaves the tree in this state:

1. `git status` shows only the files you intended to change.
2. The file(s) you edited pass `python -c "import ast; ast.parse(...)"` (Python) or `npx tsc --noEmit` (TS).
3. Targeted tests pass — `pytest tests/<area> -v` for Python, `npm run build` exits 0 for SPA.
4. If SPA changed, the new bundle hash appears in `web_dist/index.html`.
5. `git diff --stat` shows reasonable line counts (no accidental 2000-line wipe).

If any of those don't hold, recover with `git checkout HEAD -- <path>`
and retry with a more conservative edit strategy.

## References

- `references/patch-tool-quirks.md` — concrete reproduction transcripts and per-tool behavior matrix for the patch tool
- `references/spa-bundle-hash.md` — content-hash filename scheme and how to confirm a SPA change shipped