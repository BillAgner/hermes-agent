"""One-shot fix for the unquoted-colon-in-description YAML bug.

Background: _validate_frontmatter() in tools/skill_manager_tool.py runs
yaml.safe_load() on the SKILL.md frontmatter and rejects anything that
doesn't parse. The lessons-manage description contains unquoted colons
("rule: when X happens") which YAML reads as a nested mapping, so every
pending patch against it fails the structural check.

This script:
1. Backs up the affected files
2. Quotes the `description:` value (turns the unquoted string into a
   JSON-quoted string) — minimal change, no other modifications
3. Quotes the description in the pending obsidian edit's payload so the
   new content itself passes validation
4. Verifies both frontmatters parse

Then run /skills approve <id> for 6211db08, 4230152f, 25dd7e70.
"""

import json
import re
import shutil
from pathlib import Path

import yaml


HERMES_HOME = Path(r"C:\Data\Hermes_0.17.0")


def quote_description(content: str) -> tuple[str, int]:
    """Replace the first unquoted `description: <value>` line with a quoted one.

    Returns (new_content, replacement_count).
    """
    return re.subn(
        r"^description: (.+)$",
        lambda m: "description: " + json.dumps(m.group(1)),
        content,
        count=1,
        flags=re.MULTILINE,
    )


def verify_frontmatter(path: Path, label: str) -> None:
    """Read SKILL.md, parse frontmatter, raise on failure."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise SystemExit(f"{label}: missing leading ---")
    m = re.search(r"\n---\s*\n", content[3:])
    if not m:
        raise SystemExit(f"{label}: frontmatter not closed")
    yaml_content = content[3 : m.start() + 3]
    parsed = yaml.safe_load(yaml_content)
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label}: frontmatter did not parse as mapping")
    if "description" not in parsed:
        raise SystemExit(f"{label}: missing description field")
    print(f"  {label}: parses OK, desc_len={len(parsed['description'])}")


def main() -> None:
    # 1. Backups
    targets = [
        HERMES_HOME / "skills" / "lessons-manage" / "SKILL.md",
        HERMES_HOME / "skills" / "note-taking" / "obsidian" / "SKILL.md",
    ]
    for p in targets:
        bak = p.with_suffix(p.suffix + ".pre-frontmatter-fix.bak")
        if bak.exists():
            print(f"  backup already exists: {bak}")
        else:
            shutil.copy(p, bak)
            print(f"  backed up: {p.name} -> {bak.name}")

    # 2. Fix lessons-manage/SKILL.md
    lessons = HERMES_HOME / "skills" / "lessons-manage" / "SKILL.md"
    content = lessons.read_text(encoding="utf-8")
    new_content, n = quote_description(content)
    if n != 1:
        raise SystemExit(f"lessons-manage: replaced {n} description lines (expected 1)")
    lessons.write_text(new_content, encoding="utf-8")
    print(f"  wrote: {lessons}")
    verify_frontmatter(lessons, "lessons-manage/SKILL.md")

    # 3. Fix pending 25dd7e70.json payload
    pending = HERMES_HOME / "pending" / "skills" / "25dd7e70.json"
    rec = json.loads(pending.read_text(encoding="utf-8"))
    old_payload_content = rec["payload"]["content"]
    new_payload_content, n = quote_description(old_payload_content)
    if n != 1:
        raise SystemExit(f"25dd7e70 payload: replaced {n} description lines (expected 1)")
    rec["payload"]["content"] = new_payload_content
    pending.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    print(f"  wrote: {pending}")

    # Verify the new payload content parses as a full SKILL.md frontmatter
    verify_content = rec["payload"]["content"]
    m = re.search(r"\n---\s*\n", verify_content[3:])
    if not m:
        raise SystemExit("25dd7e70 payload: frontmatter not closed in new content")
    yaml_content = verify_content[3 : m.start() + 3]
    parsed = yaml.safe_load(yaml_content)
    if "description" not in parsed:
        raise SystemExit("25dd7e70 payload: missing description after quoting")
    print(
        f"  25dd7e70 payload: frontmatter parses OK, desc_len={len(parsed['description'])}"
    )

    # 4. Also check current obsidian/SKILL.md (for completeness; should already parse)
    obsidian = HERMES_HOME / "skills" / "note-taking" / "obsidian" / "SKILL.md"
    verify_frontmatter(obsidian, "obsidian/SKILL.md (unchanged)")

    print()
    print("DONE. Now run /skills approve 6211db08, /skills approve 4230152f, /skills approve 25dd7e70")


if __name__ == "__main__":
    main()