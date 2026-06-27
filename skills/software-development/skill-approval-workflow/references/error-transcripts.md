# Captured Error Transcripts — Stuck Pending Skill Writes

Real transcripts from sessions where `/skills approve <id>` failed. Each one
shows: the input, the exact error string returned by `apply_skill_pending`,
the diagnostic step that located the cause, and the fix that made the
pending apply on retry.

---

## 1. Unquoted colon in existing frontmatter (lessons-manage)

**Pending id:** `6211db08` (and chained: `4230152f`)

**Surface symptom:** Bill ran `/skills approve 6211db08` twice and
`/skills approve 25dd7e70` once — none applied. The skill stayed unchanged.

**Direct reproduction:**
```python
from tools.skill_manager_tool import apply_skill_pending
import json
with open("pending/skills/6211db08.json") as f:
    rec = json.load(f)
print(json.loads(apply_skill_pending(rec["payload"])))
```

**Error returned:**
```json
{
  "success": false,
  "error": "Patch would break SKILL.md structure: YAML frontmatter parse error: mapping values are not allowed here\n  in \"<unicode string>\", line 3, column 229:\n     ... is a conditional procedural rule: when X happens, do Y, because  ... \n                                         ^"
}
```

**Diagnosis:**
```python
import yaml
content = open("skills/lessons-manage/SKILL.md").read()
m = re.search(r"\n---\s*\n", content[3:])
yaml.safe_load(content[3:m.start() + 3])   # raises YAMLError
```

The error pointed at line 3 (the `description:` line) column 229 — inside
the literal text "rule: when X happens". The description was unquoted and
contained a `:` followed by a space, which YAML interpreted as a nested
mapping.

**Fix (single regex):**
```python
import re, json
path = r"skills/lessons-manage/SKILL.md"
content = open(path).read()
fixed = re.sub(
    r"^description: (.+)$",
    lambda m: "description: " + json.dumps(m.group(1)),
    content, count=1, flags=re.MULTILINE,
)
# Backup before write
open(path + ".bak", "w").write(content)
open(path, "w").write(fixed)
```

After the fix, `yaml.safe_load` parsed cleanly and `/skills approve
6211db08` succeeded.

**Irony:** the patch 6211db08 was itself an example of "chained-placeholder
patches from `background_review` can be DOA" — but the patch it described
couldn't apply to its own target because of an unrelated frontmatter bug
that predated the chain.

---

## 2. Unquoted colon in pending `edit` payload (obsidian)

**Pending id:** `25dd7e70`

**Surface symptom:** Same silent `/skills approve` failure pattern.

**Direct reproduction:** Same as case 1.

**Error returned:**
```json
{
  "success": false,
  "error": "YAML frontmatter parse error: mapping values are not allowed here\n  in \"<unicode string>\", line 3, column 143:\n     ... in an Obsidian vault (PARA-style: Daily/, Projects/, Inbox/, Tem ... \n                                         ^"
}
```

**Diagnosis:** The on-disk `note-taking/obsidian/SKILL.md` was clean — the
frontmatter parsed. The error was in the **pending JSON's `payload.content`
field** (because `25dd7e70` was an `edit` action with a full new SKILL.md
embedded). The new content had its own unquoted colon:
```yaml
description: Read, search, create, and edit notes in the Obsidian vault. Use when working with markdown notes in an Obsidian vault (PARA-style: Daily/, Projects/, Inbox/, Templates/, References/). ...
```

**Fix:** Edit the pending JSON directly, quoting the description in
`payload.content`:
```python
import re, json
with open("pending/skills/25dd7e70.json") as f:
    rec = json.load(f)
content = rec["payload"]["content"]
fixed = re.sub(
    r"^description: (.+)$",
    lambda m: "description: " + json.dumps(m.group(1)),
    content, count=1, flags=re.MULTILINE,
)
rec["payload"]["content"] = fixed
with open("pending/skills/25dd7e70.json", "w") as f:
    json.dump(rec, f, indent=2)
```

Note: this fix doesn't touch the on-disk file at all. Only the pending
record's `content` field.

---

## 3. Chained-placeholder patches against a renamed section header

**Surface symptom:** `/skills approve <id>` returned success=False with
`fuzzy_find_and_replace` errors. Both patches in the chain (6211db08 and
4230152f) were staged as a pair by `background_review` — the second's
`old_string` was the first's `new_string` (the "insert placeholder, then
replace" pattern used to guarantee unique-match fuzzy).

**Diagnosis:** Read each patch's `old_string`:
```
old: "## Deprecation gate for the `memory` tool (lessons auto-extraction)"
```

Then read the on-disk file. If that heading appears verbatim, the fuzzy
match should hit. If the heading was renamed in a previous (unmerged) patch
chain, the old_string doesn't match.

**Recovery:** Triage each pending in the chain individually with
`/skills diff <id>`. Decide whether to reject the chain outright, or to
re-anchor each patch's `old_string` to the current on-disk state and
re-stage.

---

## 4. `_security_scan_skill` rejection after a clean write

**Surface symptom:** `/skills approve` returns success=False with a
message starting with `"Security scan blocked"`. The file was actually
written (atomic write succeeded), then rolled back.

**Diagnosis:** Read the full error string. The security scanner reports
which pattern matched; inspect the patched content for that pattern and
decide whether it's a false positive or a real issue. Do NOT bypass.

---

## 5. `hermes skills approve <id>` "invalid choice" error

**Surface symptom:**
```
hermes skills: error: argument skills_action: invalid choice: 'approve'
(choose from browse, search, install, inspect, list, check, update, audit,
uninstall, reset, list-modified, diff, opt-out, opt-in, repair-official,
publish, snapshot, tap, config)
```

**Diagnosis:** The `hermes skills` CLI doesn't have an `approve` subcommand.
The approval surface is the gateway slash command `/skills approve <id>`,
handled by `gateway/slash_commands.py::_handle_skills_command`.

**Recovery:** Tell the user to use the slash command, not the CLI.

---

## 6. Pending JSON missing from disk

**Surface symptom:** `/skills approve <id>` returns "No pending skills
write with id '<id>'."

**Diagnosis:**
```bash
ls "$HERMES_HOME/pending/skills/<id>.json" 2>/dev/null
ls ~/.hermes/pending/skills/<id>.json       2>/dev/null
```

Hermes 0.17+ stores pending writes at `<HERMES_HOME>/pending/skills/`.
Earlier versions stored at `~/.hermes/pending/skills/`. If neither file
exists, the pending was already approved or rejected (or never written).

**Recovery:** Re-create the pending via `skill_manage(action='patch'|...)`
which will stage a new JSON with a fresh id.