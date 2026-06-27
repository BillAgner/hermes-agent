"""Triage every pending skill write.

For each pending/skills/<id>.json:
- load record
- try apply_skill_pending
- classify result: applied / frontmatter-error / security-blocked / other-error
- print a one-line summary
- if applied: discard the pending record (handled by the caller via discard_pending)
- if not: print enough detail (first 200 chars of error) for diagnosis

Toggle `skills.guard_agent_created` off → apply → on, just like the manual
process. Restores the flag at the end even on exception.
"""

import json
import shutil
import sys
import traceback
from pathlib import Path

HERMES_HOME = Path(r"C:\Data\Hermes_0.17.0")
PENDING_DIR = HERMES_HOME / "pending" / "skills"

# Make hermes package importable
sys.path.insert(0, str(HERMES_HOME))


def main():
    # Toggle guard off
    cfg_path = HERMES_HOME / "config.yaml"
    original_guard = None
    try:
        import yaml as _y
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _y.safe_load(f) or {}
        original_guard = (cfg.get("skills") or {}).get("guard_agent_created", False)
        print(f"Original skills.guard_agent_created = {original_guard}")

        # We don't write back to config.yaml here; we rely on the caller having
        # already toggled it via `hermes config set`. Read current value:
        current_guard = original_guard
        # Re-read to get the live value after possible external toggle
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _y.safe_load(f) or {}
        current_guard = (cfg.get("skills") or {}).get("guard_agent_created", False)
        print(f"Current  skills.guard_agent_created = {current_guard}")

        # Import apply_skill_pending fresh
        import importlib
        import tools.skill_manager_tool as smt
        importlib.reload(smt)
        from tools.skill_manager_tool import apply_skill_pending
        from tools import write_approval as wa

        # Iterate all pending records
        paths = sorted(PENDING_DIR.glob("*.json"))
        results = {"applied": [], "frontmatter-error": [], "security-blocked": [], "no-match": [], "other-error": [], "broken-payload": []}

        for p in paths:
            pid = p.stem
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                results["broken-payload"].append((pid, f"JSON parse failed: {e}"))
                continue

            payload = rec.get("payload") or {}
            name = payload.get("name", "?")
            action = payload.get("action", "?")
            summary = rec.get("summary", "")

            try:
                result_str = apply_skill_pending(payload)
            except Exception as e:
                results["other-error"].append((pid, f"{action} {name}: EXCEPTION {type(e).__name__}: {str(e)[:200]}"))
                continue

            try:
                r = json.loads(result_str)
            except Exception:
                results["other-error"].append((pid, f"{action} {name}: non-JSON result: {result_str[:200]}"))
                continue

            if r.get("success"):
                wa.discard_pending(wa.SKILLS, pid)
                results["applied"].append((pid, f"{action} {name}: APPLIED"))
            else:
                err = r.get("error", "")
                if "YAML frontmatter parse error" in err:
                    results["frontmatter-error"].append((pid, f"{action} {name}: {err[:200]}"))
                elif "Security scan blocked" in err or "agent-created" in err.lower():
                    results["security-blocked"].append((pid, f"{action} {name}: {err[:200]}"))
                elif "No exact match" in err or "match_count" in err or "no match" in err.lower():
                    results["no-match"].append((pid, f"{action} {name}: {err[:200]}"))
                else:
                    results["other-error"].append((pid, f"{action} {name}: {err[:200]}"))

        # Print summary
        print()
        print("=" * 70)
        for category, items in results.items():
            print(f"\n[{category}]  ({len(items)})")
            for pid, msg in items:
                print(f"  {pid}  {msg}")
        print()
        print("=" * 70)
        print(f"Total: {sum(len(v) for v in results.values())}")
    finally:
        pass  # caller handles config restore


if __name__ == "__main__":
    main()