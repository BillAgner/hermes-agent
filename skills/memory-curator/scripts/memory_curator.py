#!/usr/bin/env python
"""
Memory Curator — audit Hermes memory entries and propose migrations.

The `memory` tool (MEMORY.md / USER.md) has a hard char limit (4,000 default,
now 8,000 on this host) and EVERY byte is injected into the system prompt on
every turn. Long-tail knowledge (procedures, source maps, reference tables)
should live in **skills** (loaded on demand) or **lessons** (conditional
rules), not in the system-prompt block.

This script:
  1. Reads MEMORY.md and USER.md from <hermes_home>/memories/
  2. Splits into §-delimited entries (matches the tool's parser)
  3. Classifies each entry: KEEP / MIGRATE_TO_SKILL / MIGRATE_TO_LESSON / COMPRESS
  4. Prints a self-verifying report and (with --apply) writes changes

Self-verifying: ends with a clear [OK] or [FAIL] line. Safe to wire into a
cron (hermes cron entry with no_agent=True and the --json flag).

Usage:
  # Dry-run audit (default; safe)
  python memory_curator.py

  # Apply the proposed KEEP/MIGRATE/COMPRESS actions
  python memory_curator.py --apply

  # Migrate one entry to a new skill (writes SKILL.md, removes from memory)
  python memory_curator.py --create-skill tradingview-desktop --entry-index 0

  # Machine-readable output for cron / dashboard
  python memory_curator.py --json

  # Only one target file
  python memory_curator.py --target memory
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Matches the delimiter used by tools/memory_tool.py:ENTRY_DELIMITER
ENTRY_DELIMITER = "\n§\n"
SECTION_SIGN = "§"

# Heuristic thresholds — tuned by hand, not magic. Adjust if you find
# false-positives (over-eager migration) or false-negatives (entries the
# script leaves alone that should have moved).
LONG_ENTRY_CHARS = 200          # entry length above which we suspect "verbose"
VERY_LONG_ENTRY_CHARS = 350     # very verbose — strong migration signal
TINY_ENTRY_CHARS = 60           # below this is a high-signal fact, always KEEP

# Markers that suggest an entry is a *procedure* (should be a skill) rather
# than a *fact* (should stay in memory).
PROCEDURE_MARKERS = re.compile(
    r"(?:"                                                # group alternatives
    r"`[A-Za-z][A-Za-z0-9_/.\-]+\.[A-Za-z0-9]{1,5}`"      # .py / .ps1 / .yaml
    r"|`--[a-z][a-z\-]+"                                  # CLI flags
    r"|powershell -File|cmd //c|bash -c|python -m"         # exact invocations
    r"|register-hermes|hermes config|hermes setup"         # Hermes-CLI
    r"|\d+\.\s+[A-Z]"                                     # numbered step
    r"|`-[A-Z][a-zA-Z\-]+`"                               # single-dash flag
    r")"
)

# Markers that suggest an entry is a *rule* (should be a lesson).
RULE_MARKERS = re.compile(
    r"(?:"                                                # group alternatives
    r"\bNOT\s+`|`NOT\b"                                   # explicit counterexample
    r"|\bon Windows\b.*\bNOT\b"                            # scoped rule w/ exception
    r"|counterexample|regression test"
    r"|\bif .* fails?\b"                                  # conditional branch
    r"|\bdo NOT\b|\bnever\b|\bonly when\b"
    r")",
    re.IGNORECASE,
)

# Markers that suggest an entry is a *reference table* (URLs, paths, lists)
# — better as a skill that loads on demand than always-on prompt bytes.
REFERENCE_MARKERS = re.compile(
    r"(?:"
    r"https?://"                                           # URL
    r"|`[A-Z]:\\[^\s`]+`"                                 # absolute Windows path
    r"|`C:\\Program Files"                                 # known host paths
    r"|Path pattern:|Path:|Endpoint:"
    r")"
)


@dataclass
class Entry:
    index: int
    text: str
    chars: int
    classification: str = "KEEP"
    reasons: list[str] = field(default_factory=list)
    target: Optional[str] = None  # skill/lesson name if migration proposed


def _read_entries(path: Path) -> list[str]:
    """Read a memory file and split into entries, matching memory_tool's parser."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    parts = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
    return [e for e in parts if e]


def _classify(text: str) -> tuple[str, list[str], Optional[str]]:
    """Return (classification, reasons, suggested_target_skill_name)."""
    n = len(text)
    reasons: list[str] = []
    proc_hits = len(PROCEDURE_MARKER_HITS := PROCEDURE_MARKERS.findall(text))
    rule_hits = len(RULE_MARKERS.findall(text))
    ref_hits = len(REFERENCE_MARKERS.findall(text))

    # Tiny high-signal fact — never migrate.
    if n <= TINY_ENTRY_CHARS:
        return "KEEP", [f"tiny ({n} chars ≤ {TINY_ENTRY_CHARS})"], None

    # Has a counterexample or scope-limited rule shape → lesson.
    if rule_hits >= 1 and n >= 100 and n <= 350:
        skill_name = _skill_name_from_text(text)
        return "MIGRATE_TO_LESSON", [
            f"has rule/counterexample shape (rule_hits={rule_hits})",
            f"size {n} chars is lesson-shaped (100-350)",
        ], skill_name

    # Has procedure markers AND length suggests a procedure → skill.
    if proc_hits >= 2 and n >= LONG_ENTRY_CHARS:
        skill_name = _skill_name_from_text(text)
        return "MIGRATE_TO_SKILL", [
            f"has procedure markers (proc_hits={proc_hits})",
            f"length {n} chars > {LONG_ENTRY_CHARS} — verbose for prompt",
        ], skill_name

    # Pure reference info (URLs/paths) AND long → skill.
    if ref_hits >= 2 and n >= LONG_ENTRY_CHARS and proc_hits < 2:
        skill_name = _skill_name_from_text(text)
        return "MIGRATE_TO_SKILL", [
            f"reference-table shape (ref_hits={ref_hits})",
            f"length {n} chars — better loaded on demand",
        ], skill_name

    # Long but no clear shape → flag for manual review.
    if n >= VERY_LONG_ENTRY_CHARS:
        return "COMPRESS", [
            f"length {n} chars > {VERY_LONG_ENTRY_CHARS} but no clear migration target",
            "consider splitting or condensing",
        ], None

    return "KEEP", [f"length {n} chars, shape unclear"], None


def _skill_name_from_text(text: str) -> str:
    """Heuristic: pull a kebab-case name from the entry's first line."""
    first_line = text.splitlines()[0] if text else ""
    # Take the first noun-phrase-ish chunk before a colon or period.
    head = re.split(r"[:.]", first_line, maxsplit=1)[0]
    # Lowercase, replace non-alphanumeric with hyphens, collapse repeats.
    name = re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-")
    # Cap at 64 chars to satisfy skill_manager validator.
    name = re.sub(r"-+", "-", name)[:64].rstrip("-")
    return name or "migrated-entry"


def audit(entries: list[str]) -> list[Entry]:
    return [Entry(index=i, text=t, chars=len(t), **{"classification": c, "reasons": r, "target": t2})
            for i, t in enumerate(entries)
            for c, r, t2 in [_classify(t)]]


def _print_report(file_label: str, path: Path, entries: list[Entry], total_chars: int, limit: int) -> None:
    pct = (total_chars / limit * 100) if limit else 0
    print(f"\n=== {file_label} ({path}, {total_chars}/{limit} chars, {pct:.0f}%) ===")
    if not entries:
        print("  (no entries)")
        return
    print(f"  {'idx':>3}  {'chars':>5}  {'class':<22}  {'target':<32}  first 60 chars")
    print(f"  {'-'*3}  {'-'*5}  {'-'*22}  {'-'*32}  {'-'*60}")
    for e in entries:
        head = (e.text.splitlines()[0] if e.text else "")[:60]
        print(f"  {e.index:>3}  {e.chars:>5}  {e.classification:<22}  {(e.target or '-'):<32}  {head}")


def _write_entries(path: Path, entries: list[str], backup_dir: Path) -> None:
    """Atomically rewrite the memory file (matches memory_tool's _write_file pattern)."""
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, backup_dir / f"{path.name}.bak.{ts}")
    new_content = ENTRY_DELIMITER.join(entries)
    if new_content:
        new_content += ENTRY_DELIMITER
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    tmp.replace(path)


def _create_skill(skill_name: str, entry_text: str, skills_root: Path) -> Path:
    """Write a minimal SKILL.md to <skills_root>/<name>/SKILL.md and return the path."""
    skill_dir = skills_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    # Minimal but valid frontmatter — the agent will refine it.
    body = (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: \"Use when this topic comes up — auto-migrated from memory on "
        f"{datetime.now().strftime('%Y-%m-%d')} by the memory-curator. EDIT ME.\"\n"
        f"version: 0.1.0\n"
        f"author: Hermes Agent\n"
        f"license: MIT\n"
        f"metadata:\n"
        f"  hermes:\n"
        f"    tags: [migrated-from-memory]\n"
        f"    related_skills: []\n"
        f"---\n\n"
        f"# {skill_name}\n\n"
        f"## Source\n\n"
        f"This skill was extracted from `MEMORY.md` on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}. Review, restructure, and "
        f"add proper `## When to Use`, `## Pitfalls`, and `## Verification` sections.\n\n"
        f"## Original content\n\n"
        f"```\n{entry_text}\n```\n"
    )
    skill_path.write_text(body, encoding="utf-8")
    return skill_path


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    """Resolve the four paths this script touches."""
    try:
        from hermes_cli.config import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:
        home = Path("C:/Data/Hermes")
    mem_dir = home / "memories"
    skills_root = home / "skills"               # user-local skills live here on this host
    backup_dir = mem_dir / "backups"
    return mem_dir / "MEMORY.md", mem_dir / "USER.md", skills_root, backup_dir


def _read_limit(target: str) -> int:
    """Read the configured char limit for memory or user profile (8000 / 1375 by default)."""
    try:
        import yaml
        home = Path(__import__("hermes_cli.config").config.get_hermes_home())
        cfg = home / "config.yaml"
        if cfg.exists():
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            mem = data.get("memory", {}) or {}
            key = "user_char_limit" if target == "user" else "memory_char_limit"
            return int(mem.get(key, 1375 if target == "user" else 8000))
    except Exception:
        pass
    return 1375 if target == "user" else 8000


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", choices=["memory", "user", "both"], default="both",
                   help="Which file to audit (default: both)")
    p.add_argument("--apply", action="store_true",
                   help="Apply non-skill migrations (COMPRESS, MERGE). Use --create-skill to migrate to a skill.")
    p.add_argument("--create-skill", metavar="NAME",
                   help="Migrate a specific entry to a new skill. Requires --entry-index.")
    p.add_argument("--entry-index", type=int,
                   help="Index of the entry to migrate (use --create-skill together).")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")
    p.add_argument("--verbose", action="store_true", help="Print full entry text for each migration candidate.")
    args = p.parse_args(argv)

    try:
        mem_path, user_path, skills_root, backup_dir = _resolve_paths()
    except Exception as e:
        print(f"[FAIL] Could not resolve paths: {e}", file=sys.stderr)
        return 1

    targets = []
    if args.target in ("memory", "both") and mem_path.exists():
        targets.append(("MEMORY", mem_path, _read_limit("memory")))
    if args.target in ("user", "both") and user_path.exists():
        targets.append(("USER", user_path, _read_limit("user")))

    if not targets:
        print(f"[FAIL] No memory files found at {mem_path.parent}", file=sys.stderr)
        return 1

    # --- Special path: --create-skill ---
    if args.create_skill:
        if args.entry_index is None:
            print("[FAIL] --create-skill requires --entry-index N", file=sys.stderr)
            return 1
        # Always operates on MEMORY.md (USER.md entries are user-profile facts,
        # not procedures — don't migrate those without explicit instruction).
        entries = _read_entries(mem_path)
        if args.entry_index < 0 or args.entry_index >= len(entries):
            print(f"[FAIL] entry-index {args.entry_index} out of range (file has {len(entries)} entries)",
                  file=sys.stderr)
            return 1
        entry_text = entries[args.entry_index]
        try:
            skill_path = _create_skill(args.create_skill, entry_text, skills_root)
        except Exception as e:
            print(f"[FAIL] Could not write skill: {e}", file=sys.stderr)
            return 1
        # Remove that entry and rewrite.
        remaining = [e for i, e in enumerate(entries) if i != args.entry_index]
        try:
            _write_entries(mem_path, remaining, backup_dir)
        except Exception as e:
            print(f"[FAIL] Could not update memory file: {e}", file=sys.stderr)
            return 1
        new_size = mem_path.stat().st_size if mem_path.exists() else 0
        print(f"[OK] Migrated entry {args.entry_index} to skill: {skill_path}")
        print(f"[OK] MEMORY.md now {new_size} chars (was {_read_entries(mem_path) and 0 or 0})")
        return 0

    # --- Normal audit path ---
    report: dict = {"files": [], "errors": []}
    for label, path, limit in targets:
        raw_entries = _read_entries(path)
        entries = audit(raw_entries)
        total_chars = sum(e.chars for e in entries)
        if not args.json:
            _print_report(label, path, entries, total_chars, limit)
            if args.verbose:
                for e in entries:
                    if e.classification != "KEEP":
                        print(f"\n  --- entry {e.index} ({e.classification}, target={e.target}) ---")
                        for r in e.reasons:
                            print(f"      reason: {r}")
                        print(f"      text: {e.text[:200]}{'...' if len(e.text) > 200 else ''}")
        report["files"].append({
            "label": label,
            "path": str(path),
            "limit": limit,
            "total_chars": total_chars,
            "pct": round(total_chars / limit * 100, 1) if limit else 0,
            "entries": [asdict(e) for e in entries],
        })

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    # Summary. Goes to stderr so `--json` mode has clean stdout for piping.
    total_migrations = sum(
        1 for f in report["files"] for e in f["entries"] if e["classification"] != "KEEP"
    )
    print(file=sys.stderr)
    if total_migrations == 0:
        print(f"[OK] Memory is healthy — no migrations proposed.", file=sys.stderr)
        return 0
    print(f"[OK] Audit complete. {total_migrations} candidate(s) for migration. "
          f"Use --create-skill NAME --entry-index N to extract; --apply for compress/merge.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
