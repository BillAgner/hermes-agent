"""Review pending lesson candidates from pending_lessons.yaml.

CLI flow:
  1. Load pending_lessons.yaml.
  2. If empty, print 'no pending lessons' and exit 0.
  3. For each candidate, print a summary (id, trigger, action,
     counterexample_conditions) and prompt y/n via stdin.
  4. On approve: append a fresh L-* lesson to lessons.yaml with
     status='active', preserving the candidate's trigger / action /
     rationale / scope / counterexample_conditions, and bumping
     observed_count to 1 if missing.
  5. On reject: drop the candidate (it is removed from
     pending_lessons.yaml).
  6. After all candidates processed, persist the updated
     pending_lessons.yaml (or leave it as []).

This script is intentionally small and dependency-light: stdlib
yaml + pathlib only. It is safe to run by hand or from a cron.
"""

from __future__ import annotations

import datetime as _dt
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import yaml

HERMES_HOME = Path(r"C:\Data\Hermes_0.17.0")
PENDING_PATH = HERMES_HOME / "pending_lessons.yaml"
LESSONS_PATH = HERMES_HOME / "lessons.yaml"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _new_lesson_id() -> str:
    return "L-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _load_yaml_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        raise SystemExit(f"{path} must be a YAML list, got {type(data).__name__}")
    return list(data)


def _dump_yaml(path: Path, data: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _print_candidate(idx: int, total: int, cand: Dict[str, Any]) -> None:
    cid = cand.get("id", f"<candidate-{idx+1}>")
    trigger = (cand.get("trigger") or "").strip()
    action = (cand.get("action") or "").strip()
    counter = (cand.get("counterexample_conditions") or "").strip()
    scope = (cand.get("scope") or "").strip()
    print("=" * 72)
    print(f"[{idx+1}/{total}] id: {cid}")
    print(f"  trigger: {trigger}")
    print(f"  action:  {action}")
    print(f"  scope:   {scope}")
    print(f"  counterexample_conditions: {counter}")
    print("=" * 72)


def _prompt_decision(cand: Dict[str, Any]) -> str:
    while True:
        try:
            ans = input(f"  approve '{cand.get('id', '?')}'? [y/n/q]: ").strip().lower()
        except EOFError:
            print("\n  stdin closed — defaulting to reject")
            return "n"
        if ans in ("y", "yes"):
            return "y"
        if ans in ("n", "no"):
            return "n"
        if ans in ("q", "quit"):
            return "q"
        print("  please answer y, n, or q")


def _promote_to_active(cand: Dict[str, Any]) -> Dict[str, Any]:
    """Build a lessons.yaml entry from a pending candidate."""
    return {
        "id": _new_lesson_id(),
        "trigger": cand.get("trigger", "").strip(),
        "action": cand.get("action", "").strip(),
        "rationale": cand.get("rationale", "").strip(),
        "scope": cand.get("scope", "").strip(),
        "counterexample_conditions": cand.get("counterexample_conditions", "").strip(),
        "observed_count": int(cand.get("observed_count", 1) or 1),
        "importance": float(cand.get("importance", 0.5) or 0.5),
        "tags": list(cand.get("tags", []) or []),
        "last_validated": _now_iso(),
        "source_episodes": list(cand.get("source_episodes", []) or []),
        "status": "active",
        "profile_id": cand.get("profile_id", "default"),
    }


def main() -> int:
    pending = _load_yaml_list(PENDING_PATH)
    if not pending:
        print("no pending lessons")
        return 0

    lessons = _load_yaml_list(LESSONS_PATH)
    remaining: List[Dict[str, Any]] = []
    approved = 0
    rejected = 0

    total = len(pending)
    print(f"reviewing {total} pending lesson candidate(s)\n")

    for idx, cand in enumerate(pending):
        _print_candidate(idx, total, cand)
        decision = _prompt_decision(cand)
        if decision == "q":
            # Keep the rest of the candidates in pending for next run.
            remaining.extend(pending[idx:])
            print(f"  quit — {len(remaining)} candidate(s) left for next run")
            break
        if decision == "y":
            lessons.append(_promote_to_active(cand))
            approved += 1
            print(f"  -> approved and added to {LESSONS_PATH.name}")
        else:
            rejected += 1
            print("  -> rejected")

    _dump_yaml(LESSONS_PATH, lessons)
    _dump_yaml(PENDING_PATH, remaining)

    print()
    print(f"done. approved={approved} rejected={rejected} remaining={len(remaining)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
