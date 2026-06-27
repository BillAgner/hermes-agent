---
name: obsidian
description: Read, search, create, and edit notes in the Obsidian vault.
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

## Current state on this box (verified 2026-06-25)

- `OBSIDIAN_VAULT_PATH` env var is **unset** and `~/Documents/Obsidian Vault/` does **not exist**.
- The skill is functional but ungrounded — there's no vault to read or write.
- If a session needs the vault and it's missing, bootstrap one (see below) before doing real work.

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

## Pitfalls

- **No vault, no skill.** Don't assume the skill works without verifying `OBSIDIAN_VAULT_PATH` resolves to an existing directory. Check first; if missing, offer to bootstrap.
- **Paths with spaces.** Vault paths commonly contain spaces (`Documents\Obsidian Vault\`). Always quote them in shell commands and pass absolute resolved paths to file tools — never pass `$OBSIDIAN_VAULT_PATH` raw.
- **Default location assumption.** New users often expect `~/Documents/Obsidian Vault` to exist. Obsidian can open any folder as a vault, so non-default locations work — but ask the user before creating one outside the conventional path.

## Searching the vault (QMD composition)

The vault is indexed as the `obsidian` collection by QMD. Use the `qmd` skill (`mcp__qmd__*` tools) for hybrid search instead of re-implementing search here:

- Exact phrase / code / error string -> `qmd_search` (BM25)
- Paraphrase / concept -> `qmd_vector_search`
- Best-effort recall across KB -> `qmd_deep_search`
- Have a docid from a prior search -> `qmd_get`
- Bulk fetch by glob -> `qmd_multi_get`

For ad-hoc grep/filename lookups during note editing, `search_files` is still fine.

## Vault bootstrap

If `OBSIDIAN_VAULT_PATH` is unset, the bootstrap target is the standard Obsidian location: `C:\Users\bobup\Documents\Obsidian Vault\`.

The `scripts\install_qmd.ps1` script creates this vault with a PARA layout:

- `Daily/` - daily notes
- `Projects/` - active project notes
- `Inbox/` - quick capture
- `Templates/` - `daily.md`, `project.md`
- `References/` - reference material

A `.obsidian/` config dir with `app.json` + `appearance.json` is also created. Community plugins (Dataview, Templater, Recent Files, Markdownlint) can be enabled from the Obsidian GUI after the vault is opened.

## Plugins worth enabling

- **Dataview** - query notes like a DB (e.g. list all projects with status=active)
- **Templater** - auto-fill `Daily/*.md` with date, weather, todos
- **Recent Files** - quick navigation
- **Markdownlint** - keeps notes consistent

These are GUI-side; this skill doesn't depend on them.

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
