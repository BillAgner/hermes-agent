---
name: memory-curator
description: "Use when the `memory` tool is getting full (above 80% of its char cap), when an entry is too verbose to belong in the system prompt, when you want to extract a procedure into a skill, or when running periodic memory hygiene. The `memory` tool's content is injected into the system prompt on EVERY turn — high-signal facts belong there, but procedures and reference tables should live in skills (loaded on demand) or lessons (conditional rules). This skill audits `MEMORY.md` and `USER.md`, classifies each entry, and either auto-migrates verbose procedures into new SKILL.md files or proposes manual review."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [memory, curator, self-improvement, skills, lessons, retention, hygiene]
    related_skills: [lessons-manage, hermes-agent-skill-authoring, commodity-inventory-monitor]
---

# Memory Curator

Periodic housekeeping for the `memory` tool. Hermes' system-prompt memory block is a *narrow waist* — every byte is on every turn, so the entry mix matters: high-signal short facts in memory, verbose procedures in skills, conditional rules in lessons. This skill is the migration path between them.

## When to use this skill

- "Memory is tight / I'm at 90% of the cap" → run an audit
- "Move that TradingView launch procedure out of memory into a skill" → `--create-skill`
- "Audit the memory file" / "curate" / "hygiene" → dry-run audit
- After adding a new high-traffic skill (the source map it replaced should leave memory)
- On a weekly cron if you want it to happen automatically (script supports `--json` for machine consumption)

## Don't use this skill for

- Single-entry tweaks → use the `memory` tool directly (`action=replace`)
- Lessons (conditional rules) → use the `lessons-manage` skill / tool
- Adding new entries → use the `memory` tool

## The recipe

### 1. Run a dry-run audit (safe, no writes)

```bash
python "C:\Data\Hermes\skills\memory-curator\scripts\memory_curator.py"
```

Output: a per-file table of every entry, classified as `KEEP`, `MIGRATE_TO_SKILL`, `MIGRATE_TO_LESSON`, or `COMPRESS`, with the char count and a one-line rationale. Ends with `[OK] Memory is healthy — no migrations proposed.` or `[OK] Audit complete. N candidate(s) for migration.`

Add `--verbose` to print the full text of every migration candidate. Add `--json` for machine-readable output (good for a dashboard or cron).

### 2. Migrate an entry to a new skill

```bash
python "C:\Data\Hermes\skills\memory-curator\scripts\memory_curator.py" \
    --create-skill tradingview-desktop \
    --entry-index 0
```

Behavior:
1. Reads `MEMORY.md` from `<hermes_home>/memories/`
2. Extracts entry at index 0
3. Writes a stub `C:\Data\Hermes\skills\tradingview-desktop\SKILL.md` with the entry verbatim in a fenced block
4. Removes entry 0 from `MEMORY.md` (atomic write with backup at `memories/backups/MEMORY.md.bak.<ts>`)
5. Prints `[OK] Migrated entry 0 to skill: <path>`

The stub skill has placeholder frontmatter and a "Source" section noting the migration. The agent (or you) should then re-author the stub into a proper SKILL.md following the `hermes-agent-skill-authoring` skill's spec (frontmatter, `## When to Use`, `## Pitfalls`, `## Verification Checklist`).

### 3. Compress or merge verbose entries

```bash
python "C:\Data\Hermes\skills\memory-curator\scripts\memory_curator.py" --apply
```

Currently `--apply` only flags `COMPRESS` candidates (entries >350 chars with no clear migration target). Compress is a *manual* operation — the script surfaces the candidates and the operator condenses them. Future versions may add auto-merge for near-duplicate entries (TBD; needs hash-based similarity).

### 4. Wire into a cron (optional)

The script is safe to run on a cron — it never writes without an explicit flag. For a weekly audit, use `no_agent=True` in the cron and `--json` so the output is the message Bill sees:

```python
# pseudo — register via hermes cron / cronjob tool
schedule = "0 9 * * 1"  # Mondays at 9 AM
script = "C:\\Data\\Hermes\\skills\\memory-curator\\scripts\\memory_curator.py"
flags = ["--json"]
no_agent = True
```

If the audit finds 0 candidates, the script exits silently (no Telegram ping). If it finds migrations, the JSON report is the message — Bill can then run `--create-skill` interactively.

## Classification heuristics

The script uses simple regex-based classification — no LLM call, so it runs in <100ms even on large memory files.

| Class | Trigger | Suggested action |
|---|---|---|
| `KEEP` | `len <= 60 chars` OR no clear migration target | Leave in memory |
| `MIGRATE_TO_LESSON` | has rule/counterexample markers AND 100 ≤ len ≤ 350 | Convert to `lessons_manage(action='add', ...)` |
| `MIGRATE_TO_SKILL` | ≥2 procedure markers AND len > 200, OR ≥2 reference markers AND len > 200 | `skill_manage(action='create', ...)` |
| `COMPRESS` | len > 350 but no clear migration target | Manual review — condense, split, or move to skill |

Markers:
- **Procedure**: `.py` / `.ps1` / `.yaml` filenames in backticks, CLI flags like `--foo`, `powershell -File`, `cmd //c`, `register-hermes-*`, `hermes config`, `hermes setup`, numbered list items
- **Rule**: explicit `NOT` / `never` / `only when` / `counterexample` / `regression test` / `if X fails`
- **Reference**: `https?://` URLs, absolute Windows paths in backticks, `Path pattern:` / `Endpoint:` labels

## Output contract

Exit codes:
- `0` = audit complete (whether or not candidates were found)
- `1` = fatal error (paths not found, --create-skill on a missing index, write failure)

Final line is always either `[OK] Memory is healthy — no migrations proposed.` or `[OK] Audit complete. N candidate(s) for migration. ...` or `[FAIL] <reason>`.

JSON output shape:
```json
{
  "files": [
    {
      "label": "MEMORY",
      "path": "C:\\Data\\Hermes\\memories\\MEMORY.md",
      "limit": 8000,
      "total_chars": 3858,
      "pct": 48.2,
      "entries": [
        {"index": 0, "chars": 230, "classification": "MIGRATE_TO_SKILL", "reasons": [...], "target": "tradingview-desktop", "text": "..."}
      ]
    }
  ],
  "errors": []
}
```

## Stack & dependencies

- Python 3.11+ (whatever the active Hermes venv ships — works in `C:\Data\Hermes\hermes-agent\.venv\Scripts\python.exe`)
- `pyyaml` (for reading `config.yaml` to get the current char limit). If missing, falls back to hardcoded defaults (8000 / 1375).
- `hermes_cli.config.get_hermes_home` — used to find `<hermes_home>/memories/` and `<hermes_home>/skills/`. If import fails, falls back to `C:/Data/Hermes`.
- No `pandas`, no LLM calls — stdlib + yaml only. Fast.

## Pitfalls

### Use the `memory` tool, not direct file edits, for non-extract migrations

The curator's `--create-skill` is safe (atomic write, canonical format). But **replacing, removing, or compressing entries via direct file edits** will cause the memory tool's drift guard to fire on the next `memory` action. The tool refuses to write, saves a snapshot, and asks you to "resolve the drift first". See "## Memory tool drift guard" below for the full pattern and recovery options.

**Rule of thumb:** use the curator for *audit*, *classification*, and *extracting* entries to new skills. Use the `memory` tool (`action=add|replace|remove`) for every other mutation.

### Don't run `--create-skill` without checking the entry first
The script extracts the entry **verbatim** into a fenced code block. The stub SKILL.md needs a full rewrite (frontmatter, When to Use, body, Pitfalls, Verification) before it's useful. Always `--verbose` first to confirm the right entry index.

### The `get_hermes_home()` import might fail
If you run this script outside the Hermes venv (e.g., system Python), the path resolution falls back to `C:/Data/Hermes` and might point at the wrong directory. The script prints the resolved path in the header — verify before applying.

### Atomic write pattern matters
The script writes to `MEMORY.md.tmp` then renames, matching `tools/memory_tool.py:_write_file`. This is the same atomic-rename pattern the memory tool uses, so concurrent reads see either the old or new file, never a partial. **Don't** "improve" this to a direct `write_text` — it can corrupt the file if a `memory` tool call is mid-flight.

### `MEMORY.md.lock` is a leftover from a crashed write
If the script or the memory tool crashed mid-write, you'll see `MEMORY.md.lock` next to `MEMORY.md`. It's safe to delete — it's an orphan. The new code uses atomic rename, so the `.lock` file shouldn't reappear.

### The 60-char threshold is a heuristic, not a rule
A 50-char entry that names a critical environment fact is high-signal. A 50-char entry that's a vague "TBD" is not. The script can't tell the difference — review `KEEP` candidates if memory is still tight after the obvious migrations.

## Memory tool drift guard

The `memory` tool has a built-in drift guard (issue #26045) that refuses to write `MEMORY.md` if the on-disk content doesn't match its in-memory state. This catches legitimate external edits (patch tool, shell append, manual edit, concurrent session), but it also means **migrations must go through the tool** to avoid the file being flagged as drifted.

### What the guard checks

1. **Round-trip** — the file's content, when re-parsed and re-serialized by the tool, must produce identical bytes
2. **Entry count** — the parsed entry count must match the in-memory state

The tool's in-memory state is loaded once at session start (via `MemoryStore.load_from_disk()`). It is **not** reloaded mid-session. Any `memory` action compares the file against this snapshot, and refuses to write if they differ.

### When the curator's `--create-skill` is safe

The curator does its own atomic write (temp file + rename) when extracting an entry. This is safe because:
- The write is atomic — concurrent reads see either the old or new file, never a partial
- The format is canonical (`\n§\n` separators) — round-trips through the tool
- The new state matches what the tool would produce via `memory(action=remove)` anyway

After `--create-skill`, the next `memory` action that touches the file succeeds because the file's parsed entries match what the tool expects (one fewer entry than before).

### When direct file edits cause drift

**Don't edit `MEMORY.md` directly** for compress / replace / remove operations. The curator's audit and `--create-skill` are safe, but if you bypass the tool to:
- Replace an entry with a shorter version
- Remove a specific entry (other than via the curator's `--create-skill` extraction)
- Compress content

…the next `memory` action will see the file's content differs from its in-memory state and refuse to write. The tool saves a snapshot to `MEMORY.md.bak.<unix_ts>` (the file's content as-it-was-when-the-guard-fired) and asks you to "resolve the drift first".

### The right workflow for non-extract migrations

For compressing, removing, or otherwise mutating entries that aren't being extracted to a new skill, use the `memory` tool directly:

```bash
# 1. Audit (read-only — safe)
python "C:\Data\Hermes\skills\memory-curator\scripts\memory_curator.py" --target memory

# 2. Use the memory tool to mutate (each call updates in-memory state + writes file atomically)
memory(action=remove,    old_text="<exact entry text>")
memory(action=replace,   old_text="<old text>", content="<new text>")
memory(action=add,       content="<new entry>")
```

Each tool call keeps the file in sync with the tool's in-memory state. No drift.

### Recovery: if you get into a drift state anyway

Sometimes drift fires anyway (concurrent session, external edit, malformed format). Two recovery paths:

**Option A — end the session (recommended).** The next session's `load_from_disk` re-reads the file cleanly, parsing whatever entries are there. Then the migrations work normally. No manual intervention, no risk of stomping on a concurrent session.

**Option B — force a clean state in this session.** Slow but works when you need the cleanup done now:
1. Revert the file to a state the tool considers clean (e.g., the bare `§` 1-entry form, if that's what the tool loaded at session start)
2. `memory(action=remove, old_text="<short identifying substring>")` — clears the entry; both file and in-memory state go to 0 entries
3. `memory(action=add, content="<entry>")` ×N for each entry you want
4. Now mutations work normally (file and in-memory state both have N entries)

Option B is ~10–15 tool calls for a typical cleanup. Use only when you can't afford to wait for session restart.

### The bare-§ format trap

If `MEMORY.md` ever ends up with bare `§` separators (no surrounding newlines), the tool's parser sees the whole file as 1 entry — the split is on `\n§\n`, not bare `§`. The in-memory state has 1 entry; the file has many; drift fires on every action.

This usually happens when:
- A shell command uses bare `§` as a separator (e.g., `awk -v RS='§'`)
- A human edits the file in a way that drops the newlines
- A different agent session writes the file with a different delimiter

To convert bare `§` back to `\n§\n`:
```python
from pathlib import Path
text = Path("memories/MEMORY.md").read_text(encoding="utf-8")
fixed = "\n§\n".join(e.strip() for e in text.split("§") if e.strip()) + "\n§\n"
Path("memories/MEMORY.md").write_text(fixed, encoding="utf-8")
```

But this is itself an external edit, so the next `memory` action will still see drift. To use this fix in-session, follow Option B above (revert → remove → re-add). Otherwise, the format fix takes effect on the next session restart (no other action needed).

## Verification checklist (run before trusting a fresh install)

- [ ] `python "C:\Data\Hermes\skills\memory-curator\scripts\memory_curator.py"` — exits 0, prints a table, ends with `[OK]`
- [ ] `python ... --json | python -m json.tool` — JSON parses cleanly
- [ ] `python ... --verbose` — full text of migration candidates appears
- [ ] `python ... --create-skill test-skill --entry-index 0` — creates `C:\Data\Hermes\skills\test-skill\SKILL.md`, removes entry 0, prints `[OK]`
- [ ] `cat C:\Data\Hermes\memories\backups\MEMORY.md.bak.<latest>` — backup of the pre-migration memory file exists
- [ ] `python ... --target user` — runs the same audit on `USER.md`

## Related

- **`lessons-manage`** — for migrating rule-shaped entries (counterexamples, scoped rules) into the lessons store
- **`hermes-agent-skill-authoring`** — frontmatter spec for in-repo skills; follow when refining a stub from `--create-skill`
- **`commodity-inventory-monitor`** — example of a well-built user-local skill (the kind of skill this curator helps create)
- The `memory` tool itself — see `hermes-agent/tools/memory_tool.py:124` for `memory_char_limit` defaults (2200 in code, 8000 on this host after 2026-06-18 bump)
