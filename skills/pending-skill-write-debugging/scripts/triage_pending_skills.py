"""Mass-triage every pending skill write.

For each `<HERMES_HOME>/pending/skills/<id>.json`:
- load record
- try apply_skill_pending (same code path `/skills approve` runs)
- classify result into buckets: applied / frontmatter-error / security-blocked
  / no-match / other-error / broken-payload
- print a one-line summary per record with enough error context to diagnose
- on success, discard the pending record via wa.discard_pending

Does NOT auto-reject broken records. Real triage still needs human eyes on the
6-ish "other-error" cases (stale anchor, empty name, no-op, duplicate create,
smoke-test placeholder) — see references/triage-case-study-2026-06-26.md.

Toggle `skills.guard_agent_created` off → apply → on, just like a manual
session would. Restores the flag at the end even on exception.

USAGE:
    python scripts/triage_pending_skills.py

PREREQUISITES:
    - `hermes config set skills.guard_agent_created false` before running
      (the script does NOT do this automatically — it expects the caller to
      have already done it, and restores to whatever value was current when
      the script started). The simplest workflow is:
        hermes config set skills.guard_agent_created false
        python scripts/triage_pending_skills.py
        hermes config set skills.guard_agent_created true
"""

import json
import sys
from pathlib import Path

HERMES_HOME = Path(r"C:\Data\Hermes_0.17.0")
PENDING_DIR = HERMES_HOME / "pending" / "skills"

BUCKETS = ("applied", "frontmatter-error", "security-blocked", "no-match",
           "other-error", "broken-payload")


def main():
    sys.path.insert(0, str(HERMES_HOME))

    # Refresh imports — the agent may have edited tools since this script
    # was first written
    import importlib
    import tools.skill_manager_tool as smt
    importlib.reload(smt)
    from tools.skill_manager_tool import apply_skill_pending
    from tools import write_approval as wa

    paths = sorted(PENDING_DIR.glob("*.json"))
    results = {bucket: [] for bucket in BUCKETS}

    for p in paths:
        pid = p.stem
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            results["broken-payload"].append(
                (pid, f"JSON parse failed: {e}")
            )
            continue

        payload = rec.get("payload") or {}
        name = payload.get("name", "?")
        action = payload.get("action", "?")

        try:
            result_str = apply_skill_pending(payload)
        except Exception as e:
            results["other-error"].append(
                (pid, f"{action} {name}: EXCEPTION {type(e).__name__}: "
                       f"{str(e)[:200]}")
            )
            continue

        try:
            r = json.loads(result_str)
        except Exception:
            results["other-error"].append(
                (pid, f"{action} {name}: non-JSON result: "
                       f"{result_str[:200]}")
            )
            continue

        if r.get("success"):
            wa.discard_pending(wa.SKILLS, pid)
            results["applied"].append((pid, f"{action} {name}: APPLIED"))
        else:
            err = r.get("error", "")
            if "YAML frontmatter parse error" in err:
                results["frontmatter-error"].append(
                    (pid, f"{action} {name}: {err[:200]}")
                )
            elif "Security scan blocked" in err or "agent-created" in err.lower():
                results["security-blocked"].append(
                    (pid, f"{action} {name}: {err[:200]}")
                )
            elif ("No exact match" in err
                  or "match_count" in err
                  or "no match" in err.lower()
                  or "Found N matches" in err
                  or "Could not find a match" in err):
                results["no-match"].append(
                    (pid, f"{action} {name}: {err[:200]}")
                )
            else:
                results["other-error"].append(
                    (pid, f"{action} {name}: {err[:200]}")
                )

    # Print summary
    print()
    print("=" * 70)
    for bucket in BUCKETS:
        items = results[bucket]
        print(f"\n[{bucket}]  ({len(items)})")
        for pid, msg in items:
            print(f"  {pid}  {msg}")
    print()
    print("=" * 70)
    total = sum(len(v) for v in results.values())
    print(f"Total: {total}")


if __name__ == "__main__":
    main()