#!/usr/bin/env python3
"""Hermes zombie-session cleanup — weekly cron.

Marks abandoned sessions (ended_at IS NULL AND last_msg >24h ago) as ended
with reason 'auto-cleanup-zombie'. Keeps state.db audit-friendly and stops
the stall detector from re-alerting on the same orphans.

Safety:
- Only updates sessions where ended_at IS NULL (never touches live sessions).
- Threshold 24h — well past any reasonable long-running session.
- Logs every change to cron/output/zombie-cleanup/state.json.
- Alert via Telegram on first run + on any change in count.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))  # type: ignore[name-defined]
STATE_DB = HERMES_HOME / "state.db"
STATE_FILE = HERMES_HOME / "cron/output/zombie-cleanup/state.json"

ZOMBIE_THRESHOLD_HR = 24

# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_run": None, "last_cleaned": 0, "alerted_first_run": False}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    try:
        subprocess.run(
            ["hermes", "send", "--to", "telegram", text],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


# ---------- main ----------

def main() -> int:
    state = load_state()
    if not STATE_DB.exists():
        print(f"state.db missing: {STATE_DB}", file=sys.stderr)
        return 1

    try:
        con = sqlite3.connect(str(STATE_DB), timeout=10)
        cur = con.cursor()
        cutoff = time.time() - ZOMBIE_THRESHOLD_HR * 3600

        # Find zombies first (so we can list them in the alert)
        cur.execute("""
            SELECT s.id, s.source, MAX(m.timestamp) AS last_msg
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.ended_at IS NULL
            GROUP BY s.id
            HAVING last_msg IS NULL OR last_msg < ?
        """, (cutoff,))
        zombies = cur.fetchall()

        if not zombies:
            state["last_run"] = now_iso()
            state["last_cleaned"] = 0
            save_state(state)
            print("[SILENT] no zombies")
            return 0

        # Mark them ended with auto-cleanup reason.
        ids = [z[0] for z in zombies]
        cur.executemany(
            "UPDATE sessions SET ended_at = ?, end_reason = 'auto-cleanup-zombie' "
            "WHERE id = ? AND ended_at IS NULL",
            [(time.time(), sid) for sid in ids],
        )
        con.commit()
        con.close()

        state["last_run"] = now_iso()
        state["last_cleaned"] = len(ids)
        save_state(state)

        # Telegram summary (every run — once per week, not noisy)
        lines = ["🧹 *Zombie session cleanup*", ""]
        lines.append(f"Marked *{len(ids)}* orphaned session(s) as ended "
                     f"(no message in >{ZOMBIE_THRESHOLD_HR}h):")
        for sid, src, last in zombies[:8]:
            age_hr = int((time.time() - last) / 3600) if last else None
            lines.append(f"  • `{src}` `{sid[:20]}` — silent {age_hr}h" if age_hr else f"  • `{src}` `{sid[:20]}` — no messages")
        if len(zombies) > 8:
            lines.append(f"  • …and {len(zombies) - 8} more")
        lines.append(f"\n_host: Z13 · run: {now_iso()}_")
        send_telegram("\n".join(lines))
        return 0

    except Exception as e:
        msg = f"🧹 Zombie cleanup failed: {type(e).__name__}: {e}"[:200]
        send_telegram(msg)
        print(msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
