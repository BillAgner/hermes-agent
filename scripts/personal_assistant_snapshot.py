#!/usr/bin/env python3
"""personal_assistant_snapshot.py — collect tasks + inbox + calendar into a
single JSON snapshot for the dashboard panel.

Reads:
  * ``C:\\Data\\Hermes_0.17.0\\personal\\tasks.db`` (SQLite, tasks-mcp)
  * ``C:\\Data\\Hermes_0.17.0\\google_token.json`` (OAuth token, google-workspace-mcp)
  * Spawns google-workspace-mcp via stdio JSON-RPC for Gmail + Calendar calls

Writes:
  * ``C:\\Data\\Hermes_0.17.0\\data\\personal_assistant_snapshot.json``

Re-run on any cadence (cron, dashboard load, on demand). Idempotent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HERMES_HOME = Path(os.environ.get("HERMES_HOME") or r"C:\Data\Hermes_0.17.0")
TASKS_DB = HERMES_HOME / "personal" / "tasks.db"
GWS_MCP = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "google-workspace-mcp.exe"
SNAPSHOT_PATH = HERMES_HOME / "data" / "personal_assistant_snapshot.json"


# --- Tasks (direct SQLite — no MCP needed for our own DB) ------------------

def collect_tasks() -> dict:
    """Read tasks.db directly. Mirrors tasks-mcp's storage.summary + list_tasks."""
    if not TASKS_DB.exists():
        return {"open": [], "due_24h": [], "overdue": [], "by_status": {}, "error": "tasks.db missing"}

    con = sqlite3.connect(str(TASKS_DB))
    con.row_factory = sqlite3.Row
    try:
        all_rows = [
            dict(r) for r in con.execute(
                "SELECT id, title, description, status, priority, due_at, project_slug, tags, "
                "created_at, completed_at FROM tasks ORDER BY "
                "  CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, "
                "  CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, priority ASC"
            ).fetchall()
        ]
        # Parse tags JSON
        for r in all_rows:
            try:
                r["tags"] = json.loads(r["tags"]) if r["tags"] else []
            except (TypeError, ValueError):
                r["tags"] = []

        now_iso = datetime.now(timezone.utc).isoformat()
        by_status = {}
        for r in all_rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1

        open_tasks = [r for r in all_rows if r["status"] == "open"]
        due_24h = [r for r in open_tasks if r["due_at"] and r["due_at"] <= _iso_plus_hours(24)]
        overdue = [r for r in open_tasks if r["due_at"] and r["due_at"] < now_iso]

        return {
            "open": open_tasks,
            "due_24h": due_24h,
            "overdue": overdue,
            "by_status": by_status,
            "error": None,
        }
    finally:
        con.close()


def _iso_plus_hours(hours: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# --- Gmail + Calendar (via google-workspace-mcp stdio JSON-RPC) -----------

def _drive_mcp_call(method: str, params: dict, timeout: float = 30.0) -> dict:
    """Spawn the google-workspace-mcp subprocess and send one JSON-RPC call.

    Returns the parsed ``result`` dict, or ``{"error": str(e)}`` on failure.
    """
    proc = subprocess.Popen(
        [str(GWS_MCP)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        # initialize
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "personal-assistant-snapshot", "version": "1.0"},
            },
        }) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": method, "params": params,
        }) + "\n")
        proc.stdin.flush()

        # The tool/call response contains content[0].text with the JSON payload.
        line = proc.stdout.readline()
        resp = json.loads(line)
        if "error" in resp:
            return {"error": resp["error"].get("message", "unknown")}
        result = resp.get("result", {})
        content = result.get("content", [])
        if content:
            try:
                return json.loads(content[0].get("text", "{}"))
            except (ValueError, TypeError):
                return {"raw": content[0].get("text", "")}
        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try: proc.kill()
            except Exception: pass


def collect_inbox() -> dict:
    """Recent inbox messages (max 10)."""
    if not GWS_MCP.exists():
        return {"messages": [], "error": f"google-workspace-mcp missing at {GWS_MCP}"}
    res = _drive_mcp_call("tools/call", {
        "name": "gws_gmail_search",
        "arguments": {"query": "in:inbox newer_than:7d", "max_results": 10},
    })
    if res.get("error"):
        return {"messages": [], "error": res["error"]}
    return {"messages": res.get("messages", []), "error": None}


def collect_calendar() -> dict:
    """Upcoming events (next 7 days, max 25)."""
    if not GWS_MCP.exists():
        return {"events": [], "error": f"google-workspace-mcp missing at {GWS_MCP}"}
    res = _drive_mcp_call("tools/call", {
        "name": "gws_calendar_list_events",
        "arguments": {"max_results": 25},
    })
    if res.get("error"):
        return {"events": [], "error": res["error"]}
    return {"events": res.get("events", []), "error": None}


def collect_health() -> dict:
    """MCP token health."""
    if not GWS_MCP.exists():
        return {"error": "google-workspace-mcp missing"}
    res = _drive_mcp_call("tools/call", {"name": "gws_health", "arguments": {}})
    return res if "error" not in res else {"error": res.get("error")}


# --- Main --------------------------------------------------------------

def main() -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"[snapshot] building at {now_iso}", file=sys.stderr)

    tasks = collect_tasks()
    inbox = collect_inbox()
    calendar = collect_calendar()
    health = collect_health()

    snapshot = {
        "generated_at": now_iso,
        "schema_version": 1,
        "tasks": tasks,
        "inbox": inbox,
        "calendar": calendar,
        "google_health": health,
    }

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # Print summary for cron logs
    open_n = len(tasks.get("open") or [])
    inbox_n = len((inbox.get("messages") or []))
    cal_n = len((calendar.get("events") or []))
    health_ok = bool(health and not health.get("error") and health.get("success"))
    print(f"[OK] snapshot written: {SNAPSHOT_PATH}", file=sys.stderr)
    print(f"      tasks open={open_n}  due_24h={len(tasks.get('due_24h') or [])}  overdue={len(tasks.get('overdue') or [])}", file=sys.stderr)
    print(f"      inbox recent={inbox_n}  calendar upcoming={cal_n}  google_health={'OK' if health_ok else 'ERROR'}", file=sys.stderr)
    return 0 if health_ok else 2


if __name__ == "__main__":
    sys.exit(main())
