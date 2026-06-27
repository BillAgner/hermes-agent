---
name: skill-approval-workflow
description: Diagnose and recover from `/skills approve <id>` failures when pending skill patches or edits return success=False. Use when `/skills approve` silently does nothing, when `apply_skill_pending` rejects a staged write, when the error is "Patch would break SKILL.md structure", or when the user says "I'm having trouble approving skill <id>". Covers the slash-command surface (gateway `/skills`, not the `hermes skills` CLI), the pending JSON format at `<HERMES_HOME>/pending/skills/<id>.json`, the three validator gates (frontmatter, fuzzy-match, security scan), the unquoted-colon-in-description YAML pitfall, and the fix-and-reapprove recovery recipe. Load BEFORE debugging a stuck pending write.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [skills, write-approval, debugging, frontmatter, validator, pending]
    related_skills: [hermes-agent-skill-authoring]
---

# Skill Approval Workflow — Diagnose Stuck Pending Writes

## Overview

When `skills.write_approval` is on, every `skill_manage` mutation stages a JSON file at `<HERMES_HOME>/pending/skills/<id>.json` instead of touching the on-disk skill. The user reviews those with `/skills pending`, `/skills approve <id>`, `/skills reject <id>`, `/skills diff <id>`. Approval replays the staged write via `apply_skill_pending()` (tools/skill_manager_tool.py), which bypasses the write-gate token and runs through the same `_patch_skill` / `_edit_skill` / `_create_skill` path as a direct call.

The replay can fail for reasons *unrelated to the staged payload*. The most common silent failure is a **pre-existing YAML frontmatter bug** in the on-disk SKILL.md — the patch only touches the body, but `_validate_frontmatter()` runs on the WHOLE file after the patch, so any pre-existing unquoted colon inside `description:` blocks every patch against that skill. This skill covers how to recognize, diagnose, and recover from that class of failure.

## When to Use

- User says "I can't approve skill X" or "/skills approve <id> silently does nothing"
- `apply_skill_pending` returns `success: False` with an error string starting with `"Patch would break SKILL.md structure"`
- Same error string starts with `"YAML frontmatter parse error"` — almost always the unquoted-colon pitfall below
- Pending skill JSON at `<HERMES_HOME>/pending/skills/<id>.json` exists but `/skills approve` discards it without applying
- Multiple pending patches against the same skill keep failing in the same way

## When NOT to Use

- For *authoring* a new SKILL.md (use `hermes-agent-skill-authoring` instead)
- For the `hermes skills` CLI hub (search/browse/install) — those are unrelated to the write-approval pipeline entirely
- For `/memory approve` — same code path but different subsystem (`memory`); the frontmatter pitfall doesn't apply there

## The Slash-Command Surface (Critical Distinction)

Two different "skills" command trees exist. Only the **gateway slash command** handles pending-write approval. The `hermes skills` CLI does NOT.

| Command | Where it lives | What it does |
|---|---|---|
| `/skills pending` | Gateway slash command (`gateway/slash_commands.py::_handle_skills_command`) | Lists pending writes |
| `/skills approve <id>` | Same | Replays the staged write |
| `/skills reject <id>` | Same | Discards the staged write |
| `/skills diff <id>` | Same | Shows what the staged write would change (truncated to 3000 chars in chat) |
| `/skills approval on\|off` | Same | Toggles the write-gate |
| `hermes skills approve <id>` | **DOES NOT EXIST** as a CLI subcommand | The `hermes skills` CLI lists {browse, search, install, inspect, list, check, update, audit, uninstall, reset, list-modified, diff, opt-out, opt-in, repair-official, publish, snapshot, tap, config} — no `approve`. Bill has hit this when he tried `hermes skills approve`. |
| `hermes skills diff <name>` | CLI | **Different** command — diffs a bundled skill vs stock; unrelated to pending writes |

If a user types `hermes skills approve <id>` and gets "invalid choice", they need the slash form instead. Tell them.

## Pending JSON File Format

```json
{
  "id": "6211db08",
  "subsystem": "skills",
  "action": "patch",
  "summary": "patch 'lessons-manage' SKILL.md (+4/-3 lines)",
  "origin": "background_review",
  "created_at": 1782418626.3356047,
  "payload": {
    "action": "patch",
    "name": "lessons-manage",
    "old_string": "...",
    "new_string": "...",
    "replace_all": false
  }
}
```

- `subsystem` is `"skills"` or `"memory"`. Only `skills` lands in `pending/skills/`.
- `payload.action` mirrors top-level `action`. For `edit` actions, `payload.content` is the *full* new SKILL.md (frontmatter + body).
- `origin` is `"background_review"` (from a cron / `background_review` agent fork) or `"foreground"` (from the live chat agent).
- On approve, `apply_skill_pending()` replays the payload via `skill_manage()`. On reject, the JSON is deleted.

## The Three Validator Gates

When `/skills approve <id>` runs, the payload flows through `apply_skill_pending` → `skill_manage(action="patch"|"edit"|"create", ...)` → action handler → then these gates:

| Gate | Where | What it rejects |
|---|---|---|
| `_validate_frontmatter` | tools/skill_manager_tool.py | Any post-mutation file whose frontmatter doesn't `yaml.safe_load()` as a mapping with `name` + `description` keys |
| `fuzzy_find_and_replace` | tools/fuzzy_match.py | `old_string` not uniquely matched in the file (patch only — not edit/create) |
| `_security_scan_skill` | tools/skill_manager_tool.py | Patterns flagged by the security scan after the write succeeds (rolls back the write on hit) |

The error string format tells you which gate fired:

- `"Patch would break SKILL.md structure: YAML frontmatter parse error: ..."` → **frontmatter gate**. Almost always the unquoted-colon pitfall.
- `"Patch would break SKILL.md structure: Frontmatter must include 'name' field"` or `must include 'description' field` or `Description exceeds N characters` → frontmatter gate, missing/oversized fields.
- `"Patch would break SKILL.md structure: SKILL.md must start with YAML frontmatter (---)"` or `frontmatter is not closed` → frontmatter gate, structural.
- Any `fuzzy_find_and_replace` error string (no-match, ambiguous match, etc.) → **fuzzy-match gate**. The `old_string` doesn't appear (or appears multiple times) in the on-disk file.
- `_security_scan_skill` rejection → **security gate**. The patched content triggered a pattern; the write was rolled back.

The first two gates run **before** the on-disk file is touched. The third runs **after** the write and rolls back on hit.

## The Unquoted-Colon-in-Description Pitfall

**This is the #1 cause of "Patch would break SKILL.md structure" failures.**

```yaml
description: Use when ... A lesson is a conditional procedural rule: when X happens, do Y, because Z, except when W. ...
                              ^
                              YAML sees this colon and thinks "rule" is a mapping key
```

YAML's plain-style rule: when an unquoted scalar value contains a `:` followed by whitespace, YAML can interpret the substring as a nested mapping key. The validator calls `yaml.safe_load()` on the frontmatter, and the parse fails with `mapping values are not allowed here`.

Symptoms:
- `_validate_frontmatter` rejects with `"YAML frontmatter parse error: mapping values are not allowed here"` pointing at a column inside the description
- The error column lands inside a phrase like `"X: when Y"` or `"Y: Z"` — anywhere a colon-space sequence appears
- The patch's own `new_string` may have the same problem (for `edit` actions where the new content replaces the whole SKILL.md)

**The fix:** always quote the `description:` value with `json.dumps()` (double quotes, escapes `\n` and `"` properly). Example regex:

```python
import re, json
content = re.sub(
    r'^description: (.+)$',
    lambda m: 'description: ' + json.dumps(m.group(1)),
    content, count=1, flags=re.MULTILINE,
)
```

After quoting, `yaml.safe_load` parses cleanly because the value is a single string scalar. Re-apply the pending patch (don't re-create it) — `apply_skill_pending` bypasses the write-gate, so the original `id` is preserved.

The same pitfall applies to any multi-line YAML field with `:` inside (rare in skills; mostly hits `description`). If you see the same error pointing at a different column in a different field, the same fix applies.

## Diagnostic Recipe (Re-runnable)

Use `scripts/diagnose_pending_skill.py` (linked below) — it extracts the frontmatter, runs `yaml.safe_load`, reports the exact column, and can auto-fix by quoting the description. Manual diagnostic:

```python
import re, yaml
path = r"C:\Data\Hermes_0.17.0\skills\<category>\<skill-name>\SKILL.md"
with open(path, encoding="utf-8") as f:
    content = f.read()
m = re.search(r'\n---\s*\n', content[3:])
yaml_content = content[3:m.start() + 3]
try:
    parsed = yaml.safe_load(yaml_content)
    print("frontmatter OK:", list(parsed.keys()))
except yaml.YAMLError as e:
    # e.problem_mark has line (0-indexed) and column (0-indexed)
    print(f"line={e.problem_mark.line}, col={e.problem_mark.column}")
    # Show the offending char
    lines = yaml_content.split('\n')
    line_text = lines[e.problem_mark.line]
    print(repr(line_text[max(0, e.problem_mark.column - 10):e.problem_mark.column + 30]))
```

If the diagnosis points inside `description:`, run the regex fix above and re-try `/skills approve <id>`.

## Recovery Decision Tree

```
/skills approve <id> failed
  ├─ Error: "Patch would break SKILL.md structure: YAML frontmatter parse error"
  │   ├─ Column inside `description:` line → unquoted-colon pitfall
  │   │   ├─ Patch only touches body → fix on-disk SKILL.md frontmatter (quote description), retry
  │   │   └─ Edit action (whole-file rewrite) → fix the `content` field in pending JSON, retry
  │   └─ Column at frontmatter start (line 1, col 0) → structural (missing `---` or closing)
  │       └─ Reconstruct the frontmatter manually; reject the pending and re-create
  ├─ Error: "Patch would break SKILL.md structure: Frontmatter must include..."
  │   └─ Missing/oversized field → reject the pending and create a new one with the correct field
  ├─ Error from fuzzy_find_and_replace (no-match, ambiguous)
  │   ├─ `old_string` was unique at staging time but isn't now → on-disk file drifted
  │   │   └─ Re-read the on-disk SKILL.md, find the new anchor, reject + re-create the pending
  │   └─ `old_string` was always non-unique → reject, fix the `old_string`, re-create
  └─ Error: _security_scan_skill rejection
      └─ The patched content triggered a pattern — investigate the actual message; don't bypass
```

The "fix on-disk, retry" path is faster than "reject + re-create" because:
- The original pending JSON keeps its `id` (so `discard_pending` after success works)
- The agent doesn't have to re-derive the patch's intent from scratch
- Background cron forks (`background_review`) won't generate a duplicate

## Common Pitfalls

1. **Trying `hermes skills approve <id>` instead of `/skills approve <id>`.** The CLI subcommand doesn't exist. The slash form goes through `gateway/slash_commands.py::_handle_skills_command` → `hermes_cli/write_approval_commands.py::_approve`. Always tell users which one to use.

2. **Assuming the staged payload is at fault when the error is in the frontmatter.** The patch's `new_string` may be perfectly clean; the frontmatter of the on-disk file is the problem. Read the on-disk file before debugging the pending JSON.

3. **Re-creating the pending instead of fixing the on-disk file.** For the unquoted-colon case, fixing the on-disk frontmatter (one regex) lets the existing pending apply. Re-creating wastes the agent's prior work and loses the original `id`.

4. **Forgetting backups when editing on-disk SKILL.md to fix frontmatter.** Save `.pre-frontmatter-fix.bak` next to the file before any edit. The fix is reversible, but only if you keep the backup.

5. **Quoting only one description line when multiple fields need it.** Some skills have `metadata.hermes.description` plus a top-level `description`; both can have colons. Run `yaml.safe_load` after the fix and confirm `parsed['description']` is a string.

6. **Touching the on-disk file while a pending patch against it is staged.** If the on-disk file changes between staging and approval, the fuzzy-match gate rejects the patch with "no-match found" even if the frontmatter is fine. The fix-and-approve sequence only works if the on-disk change is the frontmatter fix itself (which the patch's `old_string` doesn't touch — body-only patches are safe).

7. **For `edit` actions, fixing only the on-disk SKILL.md.** The pending JSON's `payload.content` field carries the full new SKILL.md, and that content's frontmatter also goes through `_validate_frontmatter`. Both the on-disk file AND the pending JSON's `content` field need valid frontmatter for the edit to apply.

8. **Bypassing `_security_scan_skill` rejection.** That gate runs *after* the write and rolls back on hit. "Fixing" by skipping the gate hides a real signal — investigate the security message instead.

## Verification Checklist

- [ ] Identified the failing gate from the error string format (frontmatter / fuzzy-match / security)
- [ ] For frontmatter gate: ran the diagnostic recipe and located the offending column
- [ ] For unquoted-colon: applied the `description:` quoting regex and verified `yaml.safe_load` parses cleanly
- [ ] Backed up the on-disk SKILL.md before any edit (`.pre-frontmatter-fix.bak`)
- [ ] For `edit` actions: also fixed `payload.content` in the pending JSON if needed
- [ ] Re-ran `/skills approve <id>` and got "Approved 1 skills write(s)."
- [ ] Verified the on-disk SKILL.md now has the patch's intended change
- [ ] Verified subsequent `_validate_frontmatter(new_content)` passes (the file is valid for future patches too)

## Files

- `scripts/diagnose_pending_skill.py` — re-runnable diagnostic. Takes a pending `<id>` (or a SKILL.md path). Runs the validator manually, reports the gate that would fire and the exact failure column. Optional `--fix` flag quotes the description and writes the file (with backup).
- `references/error-transcripts.md` — captured error transcripts from real failures (unquoted colon in `lessons-manage`, `PARA-style:` in obsidian new content, fuzzy no-match from chained-placeholder patches, etc.) and the exact fixes that worked. Worth reading once before debugging a stuck pending.

## Related Notes

- The pending JSON format and the `apply_skill_pending` replay path are documented inline in `tools/skill_manager_tool.py::apply_skill_pending` (the `_skill_gate_bypass` contextvar is the key — it bypasses `_apply_skill_write_gate` so the staged write can apply without re-staging itself).
- The slash command handler is `gateway/slash_commands.py::_handle_skills_command` (lines ~2214-2274); the CLI dispatch is `hermes_cli/write_approval_commands.py::handle_pending_subcommand`.
- Frontmatter authoring rules (size limits, required fields, peer shape) live in `hermes-agent-skill-authoring`. This skill is the runtime sibling — load that one when *writing* a SKILL.md, load this one when *debugging an approval*.