#!/usr/bin/env python3
"""
no_agent_script.py — TEMPLATE for a `--no-agent --script` hermes cron job.

Use this when the task is a single shell/PowerShell action that doesn't need
LLM reasoning (file cleanup, watchdog ping, health probe, fixture rotation).

Design rules baked into this template:
  - Idempotent: safe to run twice in a row.
  - Silent on "nothing to do" (empty stdout → silent delivery per --no-agent docs).
  - Reports ONE concise line on success so the cron-run log captures the action.
  - Writes errors to stderr so they show up in `hermes cron show` last_error.
  - Uses PowerShell via subprocess (same pattern as hermes-cleanup.py) so the
    shell quoting matches what the user would type interactively.

Replace the TODOs below with your real command and target path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# TODO: replace with your target. %LOCALAPPDATA% resolves to the user's
# AppData\Local — same value whether we go through env vars or PowerShell.
TARGET: Path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "YourTargetDir"


def human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def count_before(d: Path) -> tuple[int, int]:
    if not d.is_dir():
        return 0, 0
    total, count = 0, 0
    for entry in d.iterdir():
        if entry.is_file():
            try:
                total += entry.stat().st_size
                count += 1
            except OSError:
                pass
    return count, total


def run_ps(ps_command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command],
        capture_output=True, text=True, timeout=timeout,
    )


def main() -> int:
    if not TARGET.exists():
        return 0  # never used yet — silent.

    before_count, before_bytes = count_before(TARGET)
    if before_count == 0:
        return 0  # already clean — silent.

    # TODO: replace with the real PowerShell command. -Force suppresses prompts;
    # -ErrorAction SilentlyContinue so missing/empty/locked targets don't fail
    # the task loudly (Windows file-handle locks beat -Force; see skill pitfalls).
    ps = f"Remove-Item '{TARGET}\\*' -Force -ErrorAction SilentlyContinue"

    started = time.monotonic()
    result = run_ps(ps, timeout=120)
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        sys.stderr.write(
            f"{__name__}: PowerShell exited {result.returncode}\n"
            f"STDERR: {result.stderr.strip()}\n"
        )
        return 1

    after_count, after_bytes = count_before(TARGET)
    deleted = before_count - after_count
    freed = before_bytes - after_bytes
    print(
        f"{__name__}: deleted {deleted} file(s), freed {human_bytes(freed)} "
        f"from {TARGET} (took {elapsed:.2f}s, {after_count} remain)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
