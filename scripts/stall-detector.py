#!/usr/bin/env python3
"""Hermes stall detector.

Runs every 1-2 min via cron.

Sources are split into two categories:
  USER      telegram, tui, discord, slack, whatsapp, matrix, signal, …
            These sessions are idle between user turns — silence is normal.
            → Never alert for stalls. Zombies are auto-closed silently.

  AUTOMATED cron, subagent, api, webhook, schedule, …
            These sessions are short-lived jobs that should complete quickly.
            → Stall = try to auto-terminate via gateway API.
              Alert on Telegram ONLY if termination fails.
            → Zombies are auto-closed silently (same as USER).

Definitions:
  STALL  = ended_at IS NULL, last_message within ZOMBIE_THRESHOLD_HR but
           no message in >STALL_THRESHOLD_MIN. Session was recently alive
           but has gone silent mid-task.
  ZOMBIE = ended_at IS NULL, last_message >ZOMBIE_THRESHOLD_HR ago.
           Orphaned — agent process died without writing ended_at.

No alert is ever sent for idle USER sessions or for successfully auto-remediated
sessions. An alert fires only when auto-remediation of an AUTOMATED session fails.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
STATE_DB    = HERMES_HOME / "state.db"
STATE_FILE  = HERMES_HOME / "cron/output/stall-detector/state.json"
GATEWAY_URL = "http://127.0.0.1:9119"

STALL_THRESHOLD_MIN = 10      # silence before a session is considered stalled
ZOMBIE_THRESHOLD_HR = 2       # silence before a session is considered a zombie
TERMINATE_WAIT_S    = 8       # seconds to wait after terminate before re-checking
TIMEOUT             = 5

# Sessions whose silence is expected (user is just not typing)
USER_SOURCES = {
    "telegram", "tui", "discord", "slack", "whatsapp",
    "matrix", "signal", "mattermost", "teams", "google_chat",
}


# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_run": None, "last_alert": None, "pending_alert_fp": ""}


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


def get_gateway_token() -> str | None:
    """Read the session token embedded in the gateway dashboard page."""
    try:
        req = urllib.request.Request(f"{GATEWAY_URL}/", headers={"User-Agent": "stall-detector/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        marker = '__HERMES_SESSION_TOKEN__="'
        idx = html.find(marker)
        if idx == -1:
            return None
        start = idx + len(marker)
        end = html.index('"', start)
        return html[start:end]
    except Exception:
        return None


def gateway_delete_session(session_id: str, token: str) -> bool:
    """Call DELETE /api/sessions/{session_id}. Returns True on 200."""
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/sessions/{session_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code == 200
    except Exception:
        return False


# ---------- DB ----------

def query_sessions() -> list[dict]:
    if not STATE_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(STATE_DB), timeout=TIMEOUT)
        cur = con.cursor()
        cur.execute("""
            SELECT s.id, s.source, s.started_at,
                   MAX(m.timestamp) AS last_msg,
                   s.message_count, s.tool_call_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.ended_at IS NULL
            GROUP BY s.id
            ORDER BY last_msg DESC NULLS LAST
        """)
        cols = ["id", "source", "started_at", "last_msg", "msg_count", "tool_count"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except Exception as e:
        print(f"DB query failed: {e}", file=sys.stderr)
        return []


def close_zombies_in_db(session_ids: list[str]) -> int:
    """Mark sessions as ended. Returns count closed."""
    if not session_ids:
        return 0
    try:
        con = sqlite3.connect(str(STATE_DB), timeout=TIMEOUT)
        cur = con.cursor()
        now = time.time()
        cur.executemany(
            "UPDATE sessions SET ended_at = ?, end_reason = 'auto-cleanup-zombie' "
            "WHERE id = ? AND ended_at IS NULL",
            [(now, sid) for sid in session_ids],
        )
        count = cur.rowcount
        con.commit()
        con.close()
        return count
    except Exception as e:
        print(f"DB close failed: {e}", file=sys.stderr)
        return 0


# ---------- main ----------

def main() -> int:
    state = load_state()
    sessions = query_sessions()
    now = time.time()

    if not sessions:
        state["last_run"] = now_iso()
        state["pending_alert_fp"] = ""
        save_state(state)
        print("[SILENT] no open sessions")
        return 0

    stall_threshold_s  = STALL_THRESHOLD_MIN * 60
    zombie_threshold_s = ZOMBIE_THRESHOLD_HR * 3600

    zombies, auto_stalls, user_stalls, healthy = [], [], [], []
    for s in sessions:
        last = s["last_msg"]
        if last is None:
            continue  # never had a message — ignore
        age_s = now - last
        src = (s["source"] or "").lower()

        if age_s > zombie_threshold_s:
            zombies.append(s)
        elif age_s > stall_threshold_s:
            if src in USER_SOURCES:
                user_stalls.append(s)   # idle, not a real stall
            else:
                auto_stalls.append(s)   # automated session stuck — act on it
        else:
            healthy.append(s)

    # --- 1. Auto-close zombies silently (no Telegram alert) ---
    if zombies:
        closed = close_zombies_in_db([s["id"] for s in zombies])
        print(f"[AUTO-CLOSED] {closed} zombie session(s)")

    # --- 2. User sessions: ignore stalls, just report healthy count ---
    if user_stalls:
        srcs = ", ".join(set(s["source"] for s in user_stalls))
        print(f"[IDLE] {len(user_stalls)} user session(s) silent >{STALL_THRESHOLD_MIN}m ({srcs}) — expected, skipping")

    # --- 3. Automated stalls: try to terminate, alert only on failure ---
    failed_to_terminate = []
    if auto_stalls:
        token = get_gateway_token()
        for s in auto_stalls:
            src = s["source"] or "unknown"
            sid = s["id"]
            age_min = int((now - s["last_msg"]) / 60)
            if token:
                ok = gateway_delete_session(sid, token)
                if ok:
                    print(f"[TERMINATED] {src} {sid[:20]} (silent {age_min}m)")
                else:
                    print(f"[TERM-FAIL]  {src} {sid[:20]} (silent {age_min}m) — will alert")
                    failed_to_terminate.append((s, age_min))
            else:
                # Gateway unreachable — can't terminate, must alert
                print(f"[NO-GATEWAY] {src} {sid[:20]} (silent {age_min}m) — will alert")
                failed_to_terminate.append((s, age_min))

    state["last_run"] = now_iso()

    if not failed_to_terminate:
        state["pending_alert_fp"] = ""
        save_state(state)
        if auto_stalls:
            print(f"[OK] all {len(auto_stalls)} stalled automated session(s) terminated")
        else:
            print("[SILENT]")
        return 0

    # --- 4. Alert only for sessions we could not terminate ---
    # De-duplicate: don't re-alert for the same set of stuck sessions
    fp_parts = sorted("%s:%s" % (s["source"], s["id"][:12]) for s, _ in failed_to_terminate)
    current_fp = ",".join(fp_parts)
    last_fp = state.get("pending_alert_fp", "")

    if current_fp == last_fp:
        print("[SILENT] same stuck sessions as last alert — suppressing repeat")
        state["last_run"] = now_iso()
        save_state(state)
        return 0

    lines = ["🚨 *Hermes Stall Alert — auto-termination failed*", ""]
    lines.append(f"*{len(failed_to_terminate)} automated session(s) are stuck and could not be terminated:*")
    for s, age_min in failed_to_terminate[:8]:
        sid, src = s["id"], s["source"] or "unknown"
        lines.append(f"  • `{src}` `{sid[:20]}` — silent {age_min}m")
    if len(failed_to_terminate) > 8:
        lines.append(f"  • …and {len(failed_to_terminate) - 8} more")
    lines.append("")
    lines.append(f"_host: Z13 · run: {now_iso()} · healthy={len(healthy)}_")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_…truncated_"

    send_telegram(msg)
    state["last_alert"] = now_iso()
    state["pending_alert_fp"] = current_fp
    save_state(state)
    print("alert sent")
    return 1


if __name__ == "__main__":
    sys.exit(main())
