#!/usr/bin/env python3
"""
cleanup-rdclient-trace.py — Daily cleanup of Remote Desktop Client auto-trace ETL files.

The Microsoft Remote Desktop Client writes .etl trace files to
    %LOCALAPPDATA%\\Temp\\DiagOutputDir\\RdClientAutoTrace\\
on every connection. These accumulate fast (~9MB each, dozens per day)
and are safe to delete — they're regenerated on the next connection.

Run daily via hermes cron with --no-agent (silent on success).

Mirrors the user's manual command:
    Remove-Item "C:\\Users\\bobup\\AppData\\Local\\Temp\\DiagOutputDir\\RdClientAutoTrace\\*" -Force
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Resolve the target dir once. %LOCALAPPDATA% resolves to
# C:\Users\<user>\AppData\Local — same value whether we go through PowerShell or env vars.
TARGET_DIR = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "DiagOutputDir" / "RdClientAutoTrace"


def count_files_and_bytes(d: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) under d (non-recursive, top-level only)."""
    if not d.is_dir():
        return 0, 0
    total = 0
    count = 0
    for entry in d.iterdir():
        if entry.is_file():
            try:
                total += entry.stat().st_size
                count += 1
            except OSError:
                pass
    return count, total


def human_bytes(n: int) -> str:
    """Format byte count as KiB/MiB/GiB."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def run_ps(ps_command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a PowerShell command and return the CompletedProcess."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command],
        capture_output=True, text=True, timeout=timeout,
    )


def main() -> int:
    if not TARGET_DIR.exists():
        # First run, or never used RDP — nothing to do, stay silent.
        return 0

    before_count, before_bytes = count_files_and_bytes(TARGET_DIR)
    if before_count == 0:
        # Directory exists but is already empty — stay silent.
        return 0

    # Mirror the user's exact command. -Force suppresses per-file prompts and
    # read-only attribute errors. Single-quoted path so $env vars inside it
    # would NOT be expanded (we want the literal value we already resolved).
    ps = (
        f"Remove-Item '{TARGET_DIR}\\*' -Force -ErrorAction SilentlyContinue; "
        # Also nuke any zero-byte stray sub-dirs PowerShell might have left behind.
        f"Get-ChildItem -Path '{TARGET_DIR}' -Directory -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.GetFileSystemInfos().Count -eq 0 }} | Remove-Item -Force -ErrorAction SilentlyContinue"
    )

    started = time.monotonic()
    result = run_ps(ps, timeout=120)
    elapsed = time.monotonic() - started

    after_count, after_bytes = count_files_and_bytes(TARGET_DIR)
    deleted_count = before_count - after_count
    deleted_bytes = before_bytes - after_bytes

    if result.returncode != 0:
        # Non-zero exit — surface the error so the cron delivery shows it.
        sys.stderr.write(
            f"cleanup-rdclient-trace: PowerShell exited {result.returncode}\n"
            f"STDERR: {result.stderr.strip()}\n"
        )
        return 1

    # Quiet success — but write a one-liner so the cron run log captures the
    # action. With --no-agent, stdout is delivered verbatim, so keep it short.
    print(
        f"cleanup-rdclient-trace: deleted {deleted_count} file(s), "
        f"freed {human_bytes(deleted_bytes)} from {TARGET_DIR} "
        f"(took {elapsed:.2f}s, {after_count} remain)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
