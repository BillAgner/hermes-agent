#!/usr/bin/env python3
"""Hermes thermal probe — daily cron.

Detects if the Z13 has been thermal-throttled in the last 24h by reading
Windows event log entries from Kernel-Power and Kernel-Processor-Aggregator.

Sources checked (last 24h):
  - Microsoft-Windows-Kernel-Power/41 — "Processor performance has been
    limited due to thermal throttling or power budget exceeded"
  - Microsoft-Windows-Kernel-Processor-Aggregator — similar P-state changes

Fingerprint-dedup so persistent throttling doesn't spam.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
STATE_FILE = HERMES_HOME / "cron/output/thermal-probe/state.json"

LOOKBACK_HOURS = 24


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"alert_fingerprint": "", "last_run": None}


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


def query_events(log_name: str, event_id: int) -> list[dict]:
    """Return events from `log_name` matching `event_id` from the last 24h."""
    since = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=LOOKBACK_HOURS)
    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"Get-WinEvent -FilterHashtable @{{LogName='{log_name}';Id={event_id};StartTime='{since_str}'}} "
        f"-ErrorAction SilentlyContinue | Select-Object -First 10 TimeCreated,Message | "
        f"Format-List | Out-String"
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        text = out.stdout
    except Exception:
        return []
    # Parse "TimeCreated : ..." / "Message    : ..." pairs
    events = []
    cur: dict = {}
    for line in text.splitlines():
        m_time = re.match(r"\s*TimeCreated\s*:\s*(.+)", line)
        m_msg = re.match(r"\s*Message\s*:\s*(.+)", line)
        if m_time:
            if cur:
                events.append(cur)
                cur = {}
            cur["time"] = m_time.group(1).strip()
        elif m_msg and cur is not None:
            cur.setdefault("messages", []).append(m_msg.group(1).strip())
    if cur:
        events.append(cur)
    return events


def main() -> int:
    state = load_state()
    # Probe the two most useful log sources for thermal/power issues.
    thermal_events = query_events("System", 41)  # Kernel-Power
    aggregator_events = query_events("System", 37)  # Kernel-Processor-Aggregator (P-state)

    total = len(thermal_events) + len(aggregator_events)
    state["last_run"] = now_iso()

    if total == 0:
        # Clean — clear any prior alert fingerprint so the next real event fires.
        if state.get("alert_fingerprint"):
            state["alert_fingerprint"] = ""
            save_state(state)
        print("[SILENT] no thermal events in 24h")
        return 0

    fp = f"t={len(thermal_events)};a={len(aggregator_events)}"
    last_fp = state.get("alert_fingerprint", "")
    recovered = bool(last_fp) and not fp
    new_failure = bool(fp) and fp != last_fp

    if not new_failure and not recovered:
        save_state(state)
        print(f"[SILENT] {fp}")
        return 0

    lines = ["🌡️ *Thermal throttle detected (last 24h)*", ""]
    if thermal_events:
        lines.append(f"*Kernel-Power (event 41) — {len(thermal_events)} event(s):*")
        for e in thermal_events[:3]:
            msg = " ".join(e.get("messages", []))[:200]
            lines.append(f"  • {e.get('time','?')[:19]} — {msg}")
        lines.append("")
    if aggregator_events:
        lines.append(f"*Kernel-Processor-Aggregator (event 37) — {len(aggregator_events)} event(s):*")
        for e in aggregator_events[:3]:
            msg = " ".join(e.get("messages", []))[:200]
            lines.append(f"  • {e.get('time','?')[:19]} — {msg}")
        lines.append("")
    lines.append("_host: Z13 · consider: ventilation, surface, sustained load_")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_…truncated_"
    send_telegram(msg)
    state["alert_fingerprint"] = fp
    save_state(state)
    print(f"alert sent ({fp})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
