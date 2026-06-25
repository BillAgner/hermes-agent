"""Thin wrapper that starts hermes dashboard under pythonw.exe.

pythonw.exe sets sys.stdout = None.  uvicorn and the dashboard's own startup
prints would crash with AttributeError before port 9119 is ever bound.
This wrapper patches stdout/stderr to a log file before any import occurs.
"""
import sys
import traceback
import datetime
from pathlib import Path

LOGS_DIR = Path("C:/Data/Hermes_0.17.0/logs")
CRASH_LOG = LOGS_DIR / "dashboard-crash.log"

# Patch before any import that might touch stdout/stderr.
if sys.stdout is None:
    sys.stdout = open(LOGS_DIR / "dashboard-stdout.log", "a", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout

sys.argv = [
    str(Path(__file__).parent.parent / "hermes_cli" / "main.py"),
    "dashboard", "--skip-build", "--no-open",
]

try:
    from hermes_cli.main import main
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with CRASH_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{'='*60}\n")
        fh.write(f"CRASH at {datetime.datetime.now().isoformat()}\n")
        fh.write("=" * 60 + "\n")
        traceback.print_exc(file=fh)
    raise
