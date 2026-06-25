#!/usr/bin/env python3
"""Hermes state backup — daily cron.

Backs up the SQLite databases (state.db, cold_tier.db) using SQLite's online
.backup API (safe under WAL), copies the warm_tier.qdrant/ directory, and
keeps a rolling window:
  - daily/  : 7 most recent days
  - weekly/ : 4 most recent Sundays
  - monthly/: 3 most recent first-of-month snapshots

Fingerprint-dedup so the daily spec-monitor / weekly cron won't see "issue
appeared" when backups rotated normally.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
BACKUP_ROOT = HERMES_HOME / "cron/output/backup"
STATE_FILE = BACKUP_ROOT / "state.json"

SOURCES = [
    ("state.db",     HERMES_HOME / "state.db"),
    ("cold_tier.db", HERMES_HOME / "cold_tier.db"),
]
WARM_SOURCE = HERMES_HOME / "warm_tier.qdrant"

DAILY_KEEP = 7
WEEKLY_KEEP = 4
MONTHLY_KEEP = 3

# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_run": None, "last_daily": None, "fingerprint": ""}


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


def sqlite_backup(src: Path, dst: Path) -> tuple[bool, str]:
    """Use SQLite's online backup API — safe under WAL."""
    if not src.exists():
        return False, f"missing source: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_con = sqlite3.connect(str(src))
        dst_con = sqlite3.connect(str(dst))
        with dst_con:
            src_con.backup(dst_con)
        src_con.close()
        dst_con.close()
        return True, f"{dst.stat().st_size // 1024}KB"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def copy_warm_tier(dst: Path) -> tuple[bool, str]:
    """Qdrant storage isn't SQLite — just snapshot the directory."""
    if not WARM_SOURCE.exists():
        return False, "warm_tier.qdrant/ missing (ok if warm tier unused)"
    dst.mkdir(parents=True, exist_ok=True)
    try:
        # Snapshot individual files (qdrant uses segment files; safe to copy)
        total = 0
        for src in WARM_SOURCE.rglob("*"):
            if src.is_file():
                rel = src.relative_to(WARM_SOURCE)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                total += src.stat().st_size
        return True, f"{total // 1024}KB"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def prune(directory: Path, keep: int) -> int:
    """Delete oldest entries beyond `keep`. Returns count deleted."""
    if not directory.exists():
        return 0
    entries = sorted(
        [p for p in directory.iterdir() if p.is_dir()],
        key=lambda p: p.name,
    )
    deleted = 0
    while len(entries) > keep:
        shutil.rmtree(entries.pop(0), ignore_errors=True)
        deleted += 1
    return deleted


# ---------- main ----------

def main() -> int:
    state = load_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = now_iso()

    daily_dir = BACKUP_ROOT / "daily" / today
    weekly_dir = BACKUP_ROOT / "weekly" / today  # only created on Sundays
    monthly_dir = BACKUP_ROOT / "monthly" / today  # only created on day 1

    results = []

    # SQLite backups
    for name, src in SOURCES:
        ok, msg = sqlite_backup(src, daily_dir / src.name)
        results.append((name, ok, msg))
        if ok:
            # Mirror to weekly on Sundays, monthly on 1st
            if datetime.now(timezone.utc).weekday() == 6:  # Sunday
                sqlite_backup(src, weekly_dir / src.name)
            if datetime.now(timezone.utc).day == 1:
                sqlite_backup(src, monthly_dir / src.name)

    # Warm tier (qdrant dir)
    ok, msg = copy_warm_tier(daily_dir / "warm_tier.qdrant")
    results.append(("warm_tier", ok, msg))
    if ok and datetime.now(timezone.utc).weekday() == 6:
        copy_warm_tier(weekly_dir / "warm_tier.qdrant")
    if ok and datetime.now(timezone.utc).day == 1:
        copy_warm_tier(monthly_dir / "warm_tier.qdrant")

    # Prune
    deleted = 0
    deleted += prune(BACKUP_ROOT / "daily", DAILY_KEEP)
    deleted += prune(BACKUP_ROOT / "weekly", WEEKLY_KEEP)
    deleted += prune(BACKUP_ROOT / "monthly", MONTHLY_KEEP)

    # Fingerprint: which sources succeeded
    fp = ",".join(sorted(n for n, ok, _ in results if ok))
    state["last_run"] = now
    state["last_daily"] = today
    state["fingerprint"] = fp
    save_state(state)

    failed = [n for n, ok, msg in results if not ok and "missing" not in msg]
    if failed:
        msg = "🗄️ *Backup — partial*\n\n" + "\n".join(
            f"  ❌ `{n}`: {m}" for n, ok, m in results if not ok
        ) + f"\n_pruned {deleted} old snapshots_"
        send_telegram(msg)
        return 1

    # Silent on success unless first run
    if state.get("first_run_done"):
        print("[SILENT]")
        return 0
    state["first_run_done"] = True
    save_state(state)
    msg = "🗄️ *Backup — first run complete*\n\n" + "\n".join(
        f"  ✅ `{n}`: {m}" for n, ok, m in results if ok
    ) + f"\n_pruned {deleted} old snapshots_"
    send_telegram(msg)
    return 0


if __name__ == "__main__":
    import os  # noqa: E402
    sys.exit(main())
