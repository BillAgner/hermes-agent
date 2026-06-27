#!/usr/bin/env python3
"""Diagnose why a pending skill write won't apply.

Runs the same validators the `/skills approve` handler runs, but standalone
so you can see WHICH gate fails (frontmatter / fuzzy-match / security) and
WHERE (file + column) before approving.

Usage:
    python diagnose_pending_skill.py <pending_id>
    python diagnose_pending_skill.py <pending_id> --fix
    python diagnose_pending_skill.py --path <SKILL.md path>
    python diagnose_pending_skill.py --path <SKILL.md path> --fix-dry-run

Exit codes:
    0 = would apply cleanly
    1 = would fail (frontmatter parse error)
    2 = would fail (fuzzy no-match)
    3 = would fail (fuzzy ambiguous match)
    4 = pending JSON missing or unreadable
    5 = SKILL.md missing

With `--fix`, the script:
  1. backs up the on-disk SKILL.md to <name>.SKILL.md.pre-frontmatter-fix.bak
  2. quotes the unquoted `description:` value with json.dumps (the canonical fix)
  3. writes the file back
  4. re-runs the validator to confirm clean

It does NOT call /skills approve — you do that after the fix.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def find_pending_json(pending_id: str, hermes_home: Path) -> Path | None:
    """Locate <HERMES_HOME>/pending/skills/<id>.json. Search both the
    install-root pending/ (Hermes 0.17+) and the legacy user-profile
    pending/skills/ (~/.hermes/pending/skills/) so this works on every
    Hermes version on disk.
    """
    candidates = [
        hermes_home / "pending" / "skills" / f"{pending_id}.json",
        hermes_home / ".hermes" / "pending" / "skills" / f"{pending_id}.json",
        Path.home() / ".hermes" / "pending" / "skills" / f"{pending_id}.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def extract_frontmatter(content: str) -> tuple[str, str, str]:
    """Return (yaml_content, body, error). Mirrors _validate_frontmatter."""
    if not content.startswith("---"):
        return "", "", "SKILL.md must start with YAML frontmatter (---)."
    m = re.search(r"\n---\s*\n", content[3:])
    if not m:
        return "", "", "SKILL.md frontmatter is not closed."
    yaml_content = content[3:m.start() + 3]
    body = content[m.end() + 3:].strip()
    return yaml_content, body, ""


def diagnose_frontmatter(path: Path) -> tuple[int, str]:
    """Returns (exit_code, message). 0 = OK."""
    try:
        import yaml
    except ImportError:
        return 1, "PyYAML not installed; install with `pip install pyyaml`"

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 5, f"SKILL.md not found: {path}"

    yaml_content, body, err = extract_frontmatter(content)
    if err:
        return 1, f"frontmatter structural: {err}"

    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        # problem_mark is 0-indexed line + column
        if hasattr(e, "problem_mark"):
            line_idx = e.problem_mark.line
            col_idx = e.problem_mark.column
            lines = yaml_content.split("\n")
            if line_idx < len(lines):
                line_text = lines[line_idx]
                snippet = repr(line_text[max(0, col_idx - 20):col_idx + 30])
                return 1, (
                    f"YAML frontmatter parse error: {e.problem}\n"
                    f"  line {line_idx + 1}, col {col_idx + 1}\n"
                    f"  snippet: ...{snippet}..."
                )
        return 1, f"YAML frontmatter parse error: {e}"

    if not isinstance(parsed, dict):
        return 1, "Frontmatter must be a YAML mapping."
    if "name" not in parsed:
        return 1, "Frontmatter missing 'name' field."
    if "description" not in parsed:
        return 1, "Frontmatter missing 'description' field."
    desc = str(parsed["description"])
    if len(desc) > 1024:
        return 1, f"Description exceeds 1024 chars (got {len(desc)})."
    if not body:
        return 1, "Body is empty after frontmatter."

    return 0, f"frontmatter OK — keys: {list(parsed.keys())}, desc len={len(desc)}"


def quote_description(content: str) -> tuple[str, int]:
    """Quote the top-level `description:` value with json.dumps.

    Returns (new_content, replacements_made).
    Only matches the FIRST occurrence at the start of a line (the frontmatter
    description, not body text that happens to start with `description:`).
    """
    pattern = re.compile(r"^description: (.+)$", re.MULTILINE)
    count = 0

    def repl(m):
        nonlocal count
        count += 1
        return "description: " + json.dumps(m.group(1))

    new_content = pattern.sub(repl, content, count=1)
    return new_content, count


def diagnose_pending(pending_id: str, hermes_home: Path, fix: bool) -> int:
    pj = find_pending_json(pending_id, hermes_home)
    if pj is None:
        print(f"Pending JSON not found for id '{pending_id}' under {hermes_home}", file=sys.stderr)
        return 4

    try:
        rec = json.loads(pj.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Pending JSON unreadable: {pj} ({e})", file=sys.stderr)
        return 4

    payload = rec.get("payload", {})
    action = payload.get("action")
    name = payload.get("name")
    print(f"Pending id:    {pending_id}")
    print(f"  subsystem:   {rec.get('subsystem')}")
    print(f"  action:      {action}")
    print(f"  target:      {name}")
    print(f"  origin:      {rec.get('origin')}")
    print(f"  summary:     {rec.get('summary')}")
    print()

    # Locate the on-disk SKILL.md
    skills_root = hermes_home / "skills"
    candidates = list(skills_root.rglob(f"{name}/SKILL.md"))
    if not candidates:
        print(f"SKILL.md for '{name}' not found under {skills_root}", file=sys.stderr)
        return 5
    if len(candidates) > 1:
        print(f"Multiple SKILL.md matches for '{name}':", file=sys.stderr)
        for c in candidates:
            print(f"  {c}", file=sys.stderr)
        return 5
    skill_path = candidates[0]
    print(f"On-disk:       {skill_path}")

    # Frontmatter gate
    code, msg = diagnose_frontmatter(skill_path)
    if code == 0:
        print(f"Frontmatter:   OK ({msg})")
    else:
        print(f"Frontmatter:   FAIL — {msg}")
        if fix and "unquoted" not in msg.lower() and "description" not in msg.lower():
            print(f"\n--fix only handles unquoted-colon-in-description frontmatter errors.", file=sys.stderr)
            print(f"This error requires manual repair. Aborting without changes.", file=sys.stderr)
            return code
        if fix:
            print(f"\nAttempting fix: quoting the `description:` value with json.dumps()")
            original = skill_path.read_text(encoding="utf-8")
            fixed, n = quote_description(original)
            if n != 1:
                print(f"Expected to replace exactly 1 `description:` line; replaced {n}. Aborting.",
                      file=sys.stderr)
                return code
            bak = skill_path.with_suffix(".SKILL.md.pre-frontmatter-fix.bak")
            bak.write_text(original, encoding="utf-8")
            skill_path.write_text(fixed, encoding="utf-8")
            print(f"Backup:        {bak}")
            print(f"Wrote:         {skill_path}")
            # Re-validate
            code2, msg2 = diagnose_frontmatter(skill_path)
            if code2 == 0:
                print(f"Re-validated:  OK ({msg2})")
                print(f"\nNow run: /skills approve {pending_id}")
                return 0
            else:
                print(f"Re-validated:  STILL FAILING — {msg2}")
                # Roll back
                skill_path.write_text(original, encoding="utf-8")
                bak.unlink()
                print(f"Rolled back; manual fix required.", file=sys.stderr)
                return code2

    # Fuzzy-match gate (only for `patch` action)
    if action == "patch":
        old_string = payload.get("old_string", "")
        new_string = payload.get("new_string", "")
        content = skill_path.read_text(encoding="utf-8")
        n = content.count(old_string)
        if n == 0:
            print(f"\nFuzzy-match:   FAIL — old_string not found in on-disk file ({len(old_string)} chars)")
            print(f"  first 80 chars of old_string: {old_string[:80]!r}")
            return 2
        if n > 1:
            print(f"\nFuzzy-match:   FAIL — old_string matches {n} times (need exactly 1)")
            return 3
        print(f"Fuzzy-match:   OK (old_string matches exactly once, would apply)")

    print(f"\nVerdict:       This pending write should apply cleanly via /skills approve {pending_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pending_id", nargs="?", help="Pending id like '6211db08' (the JSON filename stem)")
    ap.add_argument("--path", help="Diagnose a SKILL.md path directly (skip pending lookup)")
    ap.add_argument("--hermes-home", default=r"C:\Data\Hermes_0.17.0",
                    help="Path to the Hermes install root (default: %(default)s)")
    ap.add_argument("--fix", action="store_true",
                    help="If the frontmatter gate fails on the unquoted-colon pitfall, quote the description and retry")
    ap.add_argument("--fix-dry-run", action="store_true",
                    help="Show what --fix would change without writing")
    args = ap.parse_args()

    hermes_home = Path(args.hermes_home)

    if args.path:
        # Diagnose a path directly
        path = Path(args.path)
        code, msg = diagnose_frontmatter(path)
        print(msg)
        if args.fix and code != 0:
            original = path.read_text(encoding="utf-8")
            fixed, n = quote_description(original)
            if args.fix_dry_run:
                print(f"\n--fix-dry-run: would replace {n} `description:` line(s):")
                # Show diff-ish
                for orig_line, new_line in zip(
                    original.splitlines()[:10], fixed.splitlines()[:10]
                ):
                    if orig_line != new_line:
                        print(f"  - {orig_line[:100]}")
                        print(f"  + {new_line[:100]}")
            else:
                bak = path.with_suffix(".SKILL.md.pre-frontmatter-fix.bak")
                bak.write_text(original, encoding="utf-8")
                path.write_text(fixed, encoding="utf-8")
                print(f"\nBackup: {bak}")
                print(f"Wrote:  {path}")
                code2, msg2 = diagnose_frontmatter(path)
                print(f"Re-validated: {msg2}")
                return code2
        return code

    if not args.pending_id:
        ap.error("either pending_id or --path is required")

    return diagnose_pending(args.pending_id, hermes_home, fix=args.fix)


if __name__ == "__main__":
    sys.exit(main())