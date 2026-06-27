---
name: obsidian
description: "Read, search, create, and edit notes in the Obsidian vault. Use when working with markdown notes in an Obsidian vault (PARA-style: Daily/, Projects/, Inbox/, Templates/, References/). Composes with the qmd skill for hybrid BM25+vector search via mcp__qmd__* tools."
platforms: [linux, macos, windows]
---

# Obsidian Vault

Use this skill for filesystem-first Obsidian vault work: reading notes, listing notes, searching note files, creating notes, appending content, and adding wikilinks.

## Vault path

Use a known or resolved vault path before calling file tools.

The documented vault-path convention is the `OBSIDIAN_VAULT_PATH` environment variable, for example from `${HERMES_HOME:-~/.hermes}/.env`. If it is unset, use `~/Documents/Obsidian Vault`.

File tools do not expand shell variables. Do not pass paths containing `$OBSIDIAN_VAULT_PATH` to `read_file`, `write_file`, `patch`, or `search_files`; resolve the vault path first and pass a concrete absolute path. Vault paths may contain spaces, which is another reason to prefer file tools over shell commands.

If the vault path is unknown, `terminal` is acceptable for resolving `OBSIDIAN_VAULT_PATH` or checking whether the fallback path exists. Once the path is known, switch back to file tools.

## Read a note

Use `read_file` with the resolved absolute path to the note. Prefer this over `cat` because it provides line numbers and pagination.

## List notes

Use `search_files` with `target: "files"` and the resolved vault path. Prefer this over `find` or `ls`.

- To list all markdown notes, use `pattern: "*.md"` under the vault path.
- To list a subfolder, search under that subfolder's absolute path.

## Search

Use `search_files` for both filename and content searches. Prefer this over `grep`, `find`, or `ls`.

- For filenames, use `search_files` with `target: "files"` and a filename `pattern`.
- For note contents, use `search_files` with `target: "content"`, the content regex as `pattern`, and `file_glob: "*.md"` when you want to restrict matches to markdown notes.

## Create a note

Use `write_file` with the resolved absolute path and the full markdown content. Prefer this over shell heredocs or `echo` because it avoids shell quoting issues and returns structured results.

## Append to a note

Prefer a native file-tool workflow when it is not awkward:

- Read the target note with `read_file`.
- Use `patch` for an anchored append when there is stable context, such as adding a section after an existing heading or appending before a known trailing block.
- Use `write_file` when rewriting the whole note is clearer than constructing a fragile patch.

For an anchored append with `patch`, replace the anchor with the anchor plus the new content.

For a simple append with no stable context, `terminal` is acceptable if it is the clearest safe option.

## Targeted edits

Use `patch` for focused note changes when the current content gives you stable context. Prefer this over shell text rewriting.

## Wikilinks

Obsidian links notes with `[[Note Name]]` syntax. When creating notes, use these to link related content.

## Searching the vault (QMD composition)

The vault is indexed as the `obsidian` collection by QMD. Use the `mcp__qmd__*` tools for hybrid search instead of re-implementing search here. The QMD MCP server actually exposes **4 tools**, not 6 — the `query` tool subsumes the search modes via a `type` field:

| MCP tool | Backend | Use when |
|---|---|---|
| `mcp__qmd__query` (with `type: "lex"`) | BM25 (FTS5) | Exact phrase, code, error string |
| `mcp__qmd__query` (with `type: "vec"`) | vector (bge-m3 via ollama) | Paraphrase / concept |
| `mcp__qmd__query` (with `type: "hyde"`) | hypothetical + vector | Complex / nuanced topics |
| `mcp__qmd__query` (mix of lex + vec + hyde) | hybrid + rerank | Best-effort recall for agent context |
| `mcp__qmd__get` | direct | Have a docid/path from prior search |
| `mcp__qmd__multi_get` | direct | Bulk fetch by glob pattern |
| `mcp__qmd__status` | meta | Index health, collection counts |

Example `query` call payload:
```json
{
  "searches": [
    {"type": "lex", "query": "connection pool"},
    {"type": "vec", "query": "why do database connections time out under load"}
  ],
  "limit": 5,
  "minScore": 0.3
}
```

For ad-hoc grep/filename lookups during note editing, `search_files` is still fine.

## Current state on this box (verified 2026-06-25)

- `OBSIDIAN_VAULT_PATH` env var **is set** (added to `$HERMES_HOME/.env`) and the vault exists at `C:\Users\bobup\Documents\Obsidian Vault\`.
- Layout: `Daily/`, `Projects/`, `Inbox/`, `Templates/` (with `daily.md` + `project.md` templates), `References/`, plus `README.md` and `.obsidian/app.json` + `appearance.json`.
- The vault is indexed as the `obsidian` collection in QMD (95 files total across `skills` + `obsidian` collections).
- If a future session finds `OBSIDIAN_VAULT_PATH` unset or the vault missing, re-run `scripts\install_qmd.ps1` (idempotent — vault step is `[SKIP]` if already present).

## Vault bootstrap (when `OBSIDIAN_VAULT_PATH` is unset)

Create a PARA-ish starter vault so the skill has a real target and QMD (or any hybrid-search tool) has something to index:

```
$OBSIDIAN_VAULT_PATH/
  .obsidian/          # app.json + appearance.json for plugin defaults
  README.md           # what this vault is for
  Daily/              # dated daily notes (YYYY-MM-DD.md)
  Projects/           # active project notes
  Inbox/              # quick capture, process later
  Templates/          # daily.md + project.md starters
  References/         # evergreen reference material
```

Then set `OBSIDIAN_VAULT_PATH` in `$HERMES_HOME/.env`. PowerShell-safe approach for the env file (avoid `Add-Content` with non-ASCII / regex chars):

```powershell
$envLine = 'OBSIDIAN_VAULT_PATH=C:\path\to\vault'
$envFile = "$env:HERMES_HOME\.env"
if (-not (Select-String -Path $envFile -Pattern '^OBSIDIAN_VAULT_PATH=' -Quiet)) {
    Add-Content -Path $envFile -Value $envLine
}
```

The `scripts\install_qmd.ps1` script does all of this automatically (Step 6 creates the vault at `C:\Users\bobup\Documents\Obsidian Vault\`, Step 7 writes the env var, Step 11 junctions the qmd skill in). Re-running it is safe — each step is `[OK]` or `[SKIP]` on subsequent runs.

## Plugins worth enabling (GUI-side, not required by this skill)

- **Dataview** - query notes like a DB (e.g. list all projects with status=active)
- **Templater** - auto-fill `Daily/*.md` with date, weather, todos
- **Recent Files** - quick navigation
- **Markdownlint** - keeps notes consistent

## Frontmatter conventions

The bootstrap templates use minimal YAML frontmatter:

```yaml
---
date: 2026-06-25
tags: [daily]
status: active
---
```

Keep frontmatter consistent so QMD's metadata-based ranking and future Dataview queries work well. QMD stores title + first heading as metadata; tags help filter.

## Pitfalls

- **No vault, no skill.** If a future session finds `OBSIDIAN_VAULT_PATH` unset or the vault missing, re-run `scripts\install_qmd.ps1` (Step 6 bootstrap is idempotent). Don't assume the skill works without verifying the vault exists.
- **Paths with spaces.** Vault paths commonly contain spaces (`Documents\Obsidian Vault\`). Always quote them in shell commands and pass absolute resolved paths to file tools — never pass `$OBSIDIAN_VAULT_PATH` raw.
- **Default location assumption.** New users often expect `~/Documents/Obsidian Vault` to exist. Obsidian can open any folder as a vault, so non-default locations work — but ask the user before creating one outside the conventional path.
- **Wrong MCP tool names.** The QMD README lists 6 tools (`qmd_search`, `qmd_vector_search`, `qmd_deep_search`, `qmd_get`, `qmd_multi_get`, `qmd_status`) but the MCP server actually exposes only **4**: `query`, `get`, `multi_get`, `status`. The search modes are folded into `query` via the `type` field. See the "Searching the vault" section above for the actual tool surface.
- **`hermes mcp restart` doesn't exist.** Use `hermes mcp test qmd` to force a reconnect of just the qmd server. New `mcp_servers` config entries are picked up LIVE without any restart step.