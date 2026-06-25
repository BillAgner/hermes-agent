"""Thin wrapper that runs hermes gateway run and captures crash traces.

pythonw.exe discards stderr, so unhandled exceptions are silently lost.
This wrapper catches them and appends a timestamped trace to gateway-crash.log
before re-raising (which terminates the process as expected).

Called by Hermes_Gateway.cmd instead of -m hermes_cli.main gateway run directly.
"""
import sys
import io
import traceback
import datetime
from pathlib import Path

CRASH_LOG = Path("C:/Data/Hermes_0.17.0/logs/gateway-crash.log")

# pythonw.exe sets stdout/stderr to None — patch before any import that touches them
if sys.stdout is None:
    sys.stdout = open(CRASH_LOG.parent / "gateway-stdout.log", "a", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout

sys.argv = [str(Path(__file__).parent.parent / "hermes_cli" / "main.py"), "gateway", "run"]

try:
    from hermes_cli.main import main
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CRASH_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{'='*60}\n")
        fh.write(f"CRASH at {datetime.datetime.now().isoformat()}\n")
        fh.write('='*60 + "\n")
        traceback.print_exc(file=fh)
    raise
