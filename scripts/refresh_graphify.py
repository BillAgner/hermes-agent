"""Daily graphify refresh — env-var guarded.

Behavior:
  1. If GEMINI_API_KEY is not set, log a noop message to stdout and
     exit 0. This is the common case when the script is run from a
     cron on a machine that does not have the Gemini client
     configured.
  2. Otherwise, refresh the merged graph. The simplest reliable
     approach on Windows is to touch a marker file at
     ``C:\\Data\\Hermes_0.17.0\\cache\\graphify_refresh.marker`` with the
     current timestamp. This lets a separate graphify process
     detect the marker and rescan. We deliberately avoid spawning
     a detached graphify watcher from this script — that would
     double-start watchers if multiple crons fire.
  3. Always append a one-line status (with timestamp) to
     ``C:\\Data\\Hermes_0.17.0\\cache\\graphify_refresh.log``.

Usage:
  python C:\\Data\\Hermes_0.17.0\\scripts\\refresh_graphify.py
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

HERMES_HOME = Path(r"C:\Data\Hermes_0.17.0")
CACHE_DIR = HERMES_HOME / "cache"
MARKER_PATH = CACHE_DIR / "graphify_refresh.marker"
LOG_PATH = CACHE_DIR / "graphify_refresh.log"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _log_status(line: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def main() -> int:
    ts = _now_iso()

    if not os.environ.get("GEMINI_API_KEY"):
        msg = f"{ts} GEMINI_API_KEY not set; skipping graphify refresh"
        print(msg)
        _log_status(msg)
        return 0

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Touch marker with the current timestamp. A separate graphify
    # watcher (or the next time the user runs `graphify --watch ...`)
    # can detect the mtime and trigger a rescan.
    MARKER_PATH.write_text(ts, encoding="utf-8")

    msg = f"{ts} GEMINI_API_KEY present; graphify_refresh.marker written"
    print(msg)
    _log_status(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
