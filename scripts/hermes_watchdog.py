#!/usr/bin/env python3
"""Hermes Watchdog v2 — self-healing service monitor with recovery history.

Replaces scripts/watchdog.py. Adds:
  - Per-MCP probes + auto-restart (voxcpm, liteparse, markitdown, open_notebook,
    tradingview, tradingview_desktop)
  - Per-container probes (qdrant, speaches, honcho-*, open-notebook-*)
  - Rich state schema with recovery_history (last 100 events)
  - Dependency map (read-only documentation) embedded in state
  - Reads config.yaml to discover MCP servers dynamically (not hard-coded)
  - Writes both the cron state file (legacy) and the new dashboard state file

State files:
  - cron/output/watchdog/state.json  -- legacy format, used by /api/health/deep
  - health/watchdog_state.json       -- new rich format, used by /api/watchdog/state

Cadence: every 60 seconds via cron (configurable via HERMES_WATCHDOG_INTERVAL env).
The legacy cron entry uses */10; this script is designed to be invoked more
frequently because per-MCP recovery should be sub-minute for user-facing services.

Modes:
  --once         Run a single probe+recovery cycle and exit (default; used by cron)
  --daemon       Run continuously with sleep loop (used by Scheduled Task)
  --recover X    Run recovery action for service X without probing first
  --status       Print current state and exit (no probes)
  --history N    Print last N recovery events and exit

Recovery philosophy (matches user preference):
  1. Probe silently.
  2. If 2+ consecutive failures -> attempt silent recovery.
  3. If recovery succeeds -> reset counter, log to recovery_history.
  4. If recovery fails 3x -> mark service as 'needs_attention' in state.
  5. Telegram alert only on:
       - state fingerprint change (new failure / recovery / composition change)
       - 'needs_attention' transition (recovery exhausted)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import yaml
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    # pythonw.exe sets sys.stdout/stderr to None.  Open a fallback log so
    # any stray print() or exception traceback has somewhere to go.
    if sys.stdout is None:
        _wd_log = open("C:/Data/Hermes_0.17.0/logs/watchdog-stdout.log", "a", encoding="utf-8")
        sys.stdout = _wd_log
        sys.stderr = _wd_log
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # Suppress console-window flash for every child process we spawn.
    # pythonw.exe has no console, so Windows would allocate one for each
    # subprocess (tasklist, docker, wsl, …) without this flag.
    _CREATE_NO_WINDOW = 0x08000000
    _orig_run = subprocess.run
    _orig_Popen = subprocess.Popen
    def _run_hidden(*a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | _CREATE_NO_WINDOW
        return _orig_run(*a, **kw)
    def _Popen_hidden(*a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | _CREATE_NO_WINDOW
        return _orig_Popen(*a, **kw)
    subprocess.run = _run_hidden
    subprocess.Popen = _Popen_hidden

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
GATEWAY_URL = "http://127.0.0.1:9119/"
PID_FILE = HERMES_HOME / "gateway.pid"
GATEWAY_SERVICE_DIR = HERMES_HOME / "gateway-service"
CONFIG_FILE = HERMES_HOME / "config.yaml"

# Legacy state file (kept for /api/health/deep compatibility)
LEGACY_STATE = HERMES_HOME / "cron/output/watchdog/state.json"
# New rich state file
RICH_STATE_DIR = HERMES_HOME / "health"
RICH_STATE_FILE = RICH_STATE_DIR / "watchdog_state.json"
# Recovery history (append-only JSONL for cheap appends + easy rotation)
RECOVERY_LOG = RICH_STATE_DIR / "recovery_history.jsonl"
# Watchdog log
WATCHDOG_LOG = HERMES_HOME / "logs/watchdog.log"

ERRORS_LOG = HERMES_HOME / "logs/errors.log"
WAL_FILE = HERMES_HOME / "state.db-wal"
COLD_TIER = HERMES_HOME / "cold_tier.db"
WARM_TIER_DIR = HERMES_HOME / "warm_tier.qdrant"
GATEWAY_LOG = HERMES_HOME / "logs/gateway.log"

ALERT_THRESHOLD = 2     # consecutive failures before Telegram alert
RESTART_THRESHOLD = 2   # consecutive failures before silent recovery attempt
RECOVERY_EXHAUSTED = 3  # failed recovery attempts before 'needs_attention'
DISK_FREE_MIN_GB = 10
WAL_STALE_MINUTES = 30
HONCHO_PORT = 8000
OPEN_NOTEBOOK_PORT = 5055
OPEN_NOTEBOOK_UI_PORT = 8502
SPEACHES_PORT = 8004
QDRANT_PORT = 6333
TIMEOUT = 8

# Hardcoded expected Docker containers (matches _common.ps1)
EXPECTED_CONTAINERS = [
    "honcho-api-1",
    "honcho-database-1",
    "honcho-redis-1",
    "honcho-deriver-1",
    "speaches",
    "qdrant-research",
    "open-notebook-local-surrealdb-1",
    "open-notebook-local-open_notebook-1",
]
WSL_MUST_BE_RUNNING = ["hermes-tools"]  # only restart what we manage

# Per-MCP recovery hint: how to detect a healthy process and respawn it.
# 'health_cmd' is run to verify liveness (returns rc 0 = healthy).
# 'pid_hint' is a substring to look for in process command lines to find/kill orphans.
# If 'health_cmd' is None, only process-based liveness is checked.
MCP_DEFAULTS: dict[str, dict] = {
    "voxcpm": {
        "health_cmd": None,  # no built-in HTTP endpoint; rely on process check
        "pid_hint": "voxcpm-mcp",
        "needs_env": ["HF_HUB_DISABLE_SYMLINKS", "VOXCPM_MODEL_ID"],
        "critical": False,  # TTS is nice-to-have, not critical
    },
    "liteparse": {
        "health_cmd": None,
        "pid_hint": "liteparse-mcp",
        "critical": False,
    },
    "markitdown": {
        "health_cmd": None,
        "pid_hint": "markitdown-mcp",
        "critical": False,
    },
    "open_notebook": {
        "health_cmd": None,
        "pid_hint": "open-notebook-mcp",
        "health_url": f"http://127.0.0.1:{OPEN_NOTEBOOK_PORT}/api/notebooks",
        "critical": True,  # research substrate
    },
    "tradingview": {
        "health_cmd": None,
        "pid_hint": "tradingview-mcp-server",
        "critical": False,
    },
    "tradingview_desktop": {
        "health_cmd": None,
        "pid_hint": "tradingview-mcp/src/server.js",
        "critical": False,
    },
}

# Dependency map (documentation; used for the dashboard 'Dependencies' view)
# Format: service -> list of services it depends on (must be healthy for this to function)
DEPENDENCY_MAP: dict[str, list[str]] = {
    "dashboard":   ["gateway"],
    "gateway":     ["docker"],
    "honcho":      ["honcho-api-1", "honcho-database-1", "honcho-redis-1"],
    "open_notebook": ["open-notebook-local-surrealdb-1", "open-notebook-local-open_notebook-1"],
    "qdrant":      ["qdrant-research"],
    "speaches":    ["speaches"],
    "memory":      ["cold_tier.db", "warm_tier.qdrant"],
    "voxcpm":      [],  # standalone local process
    "liteparse":   [],
    "markitdown":  [],
    "tradingview": [],
}


# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ts() -> str:
    """Shorter timestamp for log lines."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str, *, level: str = "INFO") -> None:
    """Append a line to the watchdog log (always, not just on failure)."""
    try:
        WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts()}  {level:5s}  {msg}\n")
    except Exception:
        pass


def load_config() -> dict:
    """Read HERMES_HOME/config.yaml, return {} on error."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_rich_state() -> dict:
    if RICH_STATE_FILE.exists():
        try:
            return json.loads(RICH_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "version": 2,
        "started_at": now_iso(),
        "last_run": None,
        "last_clean": None,
        "services": {},
        "recovery_history": [],
        "dependency_map": DEPENDENCY_MAP,
        "consecutive_failures": {},
        "recovery_attempts": {},
    }


def save_rich_state(state: dict) -> None:
    RICH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Trim history to last 100 entries to keep file size bounded
    hist = state.get("recovery_history", [])
    if len(hist) > 100:
        state["recovery_history"] = hist[-100:]
    RICH_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_recovery_event(state: dict, *, service: str, action: str, ok: bool,
                          msg: str) -> None:
    """Append to recovery history (in-memory + JSONL log)."""
    event = {
        "ts": now_iso(),
        "service": service,
        "action": action,
        "ok": ok,
        "msg": msg[:200],
    }
    state.setdefault("recovery_history", []).append(event)
    try:
        RECOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RECOVERY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception:
        pass


def fail_count(state: dict, key: str) -> int:
    return state.get("consecutive_failures", {}).get(key, 0)


def bump_fail(state: dict, key: str, failed: bool) -> None:
    cf = state.setdefault("consecutive_failures", {})
    cf[key] = (cf.get(key, 0) + 1) if failed else 0


def reset_fail(state: dict, key: str) -> None:
    state.setdefault("consecutive_failures", {})[key] = 0


def recovery_attempts(state: dict, key: str) -> int:
    return state.get("recovery_attempts", {}).get(key, 0)


def bump_recovery_attempts(state: dict, key: str, failed: bool) -> None:
    ra = state.setdefault("recovery_attempts", {})
    if failed:
        ra[key] = ra.get(key, 0) + 1
    else:
        ra[key] = 0


def send_telegram(text: str) -> None:
    """Fire-and-forget Telegram send via hermes CLI. Best-effort — never raises."""
    try:
        # Use DEVNULL instead of PIPE to avoid deadlocks when hermes spawns
        # child processes that outlive it and hold the stdout/stderr pipe open.
        subprocess.run(
            ["hermes", "send", "--to", "telegram", text],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        pass


# ---------- core probes (gateway, docker, wsl, honcho, etc.) ----------

def probe_gateway() -> tuple[bool, str]:
    """HTTP probe to gateway dashboard, plus PID liveness."""
    pid_text = PID_FILE.read_text().strip() if PID_FILE.exists() else ""
    pid = None
    for line in pid_text.splitlines():
        line = line.strip().lstrip("{").rstrip("}")
        if line.startswith('"pid"'):
            try:
                pid = int(line.split(":", 1)[1].strip().rstrip(",").strip(' "'))
            except (ValueError, IndexError):
                pass
    try:
        req = urllib.request.Request(GATEWAY_URL, headers={"User-Agent": "watchdog"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            html = r.read().decode("utf-8", errors="replace")
        ok_status = r.status == 200
        has_token = "__HERMES_SESSION_TOKEN__" in html or "Hermes Agent" in html
        pid_alive = False
        if pid:
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True, timeout=5,
                )
                pid_alive = str(pid) in out.stdout
            except Exception:
                pass
        if not ok_status:
            return False, f"HTTP {r.status}"
        if not has_token:
            return False, "HTTP 200 but no session token in HTML"
        if pid and not pid_alive:
            return False, f"HTTP 200 but PID {pid} not alive"
        return True, f"HTTP 200, PID {pid or '?'} {'alive' if pid_alive else 'unverified'}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def probe_docker() -> tuple[bool, str]:
    """Docker daemon + all expected containers in running state."""
    try:
        t0 = time.time()
        out = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=8,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        if out.returncode != 0:
            return False, f"docker daemon unreachable: {out.stderr.strip()[:100]}"
        if elapsed_ms > 5000:
            return False, f"docker daemon slow ({elapsed_ms}ms) - possible WSL/Desktop issue"
        daemon_ver = out.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "docker info timed out (daemon hung?)"
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except Exception as e:
        return False, f"docker probe failed: {type(e).__name__}: {e}"[:120]

    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return False, f"docker ps failed: {out.stderr.strip()[:80]}"
        states = {}
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                states[parts[0]] = parts[1]
        missing = [c for c in EXPECTED_CONTAINERS if c not in states]
        not_up = [c for c in EXPECTED_CONTAINERS
                  if c in states and states[c].lower() != "running"]
        if missing and not_up:
            return False, f"docker v{daemon_ver}: missing {missing}, exited {not_up}"
        if missing:
            return False, f"docker v{daemon_ver}: missing containers {missing}"
        if not_up:
            return False, f"docker v{daemon_ver}: not Up: {not_up}"
        return True, f"docker v{daemon_ver} ({elapsed_ms}ms), {len(EXPECTED_CONTAINERS)} containers Up"
    except subprocess.TimeoutExpired:
        return False, "docker ps timed out"
    except Exception as e:
        return False, f"docker ps failed: {type(e).__name__}: {e}"[:120]


def probe_wsl() -> tuple[bool, str]:
    """WSL: hermes-tools must respond to echo command."""
    # Always ping (idempotent keepalive)
    try:
        subprocess.run(
            ["wsl", "-d", "hermes-tools", "--", "/bin/true"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    active_ok = False
    active_msg = ""
    try:
        out = subprocess.run(
            ["wsl", "-d", "hermes-tools", "--", "echo", "OK"],
            capture_output=True, timeout=8,
        )
        raw = out.stdout.replace(b"\x00", b"").decode("utf-8", errors="replace").strip()
        if out.returncode == 0 and "OK" in raw:
            active_ok = True
            active_msg = "hermes-tools responds to echo"
        else:
            active_msg = f"hermes-tools echo exit {out.returncode}: {raw[:60]!r}"
    except subprocess.TimeoutExpired:
        active_msg = "hermes-tools echo timed out (>8s)"
    except FileNotFoundError:
        return False, "wsl not on PATH"
    except Exception as e:
        active_msg = f"hermes-tools echo failed: {type(e).__name__}: {e}"[:80]

    registry = {}
    try:
        out = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True, timeout=10,
        )
        if out.returncode == 0:
            raw = out.stdout.replace(b"\x00", b"").replace(b"\r", b"").decode("utf-8", errors="replace")
            for line in raw.split("\n")[1:]:
                line = line.replace("*", "").strip()
                if not line:
                    continue
                tokens = line.split()
                state = next((t for t in tokens if t in ("Running", "Stopped", "Installing", "Uninstalling")), None)
                if not state:
                    continue
                idx = tokens.index(state)
                name = " ".join(tokens[:idx]).strip()
                if name:
                    registry[name] = state
    except Exception:
        pass

    missing = [d for d in WSL_MUST_BE_RUNNING + ["docker-desktop"] if d not in registry]
    if missing:
        return False, f"WSL distros missing from registry: {missing}"

    hermes_state = registry.get("hermes-tools", "?")
    if active_ok:
        return True, f"WSL: docker-desktop={registry.get('docker-desktop','?')}, hermes-tools={hermes_state} (active OK)"
    if hermes_state == "Stopped":
        return False, f"hermes-tools Stopped AND echo failed: {active_msg}"
    return False, f"hermes-tools={hermes_state} but echo failed: {active_msg}"


def probe_honcho() -> tuple[bool, str]:
    """Honcho health probe with retry."""
    last_err = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{HONCHO_PORT}/health", timeout=3
            ) as r:
                body = r.read().decode("utf-8", errors="replace")
                if r.status == 200 and "ok" in body.lower():
                    if attempt > 1:
                        return True, f"Honcho /health OK after {attempt} attempts"
                    return True, f"Honcho /health OK ({body[:40]})"
                last_err = f"Honcho HTTP {r.status} body={body[:80]}"
        except urllib.error.URLError as e:
            last_err = f"Honcho unreachable on :{HONCHO_PORT} (attempt {attempt}/3: {e.reason})"
        except Exception as e:
            last_err = f"Honcho probe failed (attempt {attempt}/3): {e}"[:80]
        if attempt < 3:
            time.sleep(1.0)
    return False, last_err


def probe_open_notebook() -> tuple[bool, str]:
    """Open Notebook API health (research substrate)."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{OPEN_NOTEBOOK_PORT}/api/notebooks", timeout=3
        ) as r:
            return (r.status == 200), f"Open Notebook HTTP {r.status}"
    except urllib.error.URLError as e:
        return False, f"Open Notebook unreachable: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def probe_memory_tiers() -> tuple[bool, str]:
    issues = []
    if not COLD_TIER.exists():
        issues.append("cold_tier.db missing")
    if not WARM_TIER_DIR.exists():
        issues.append("warm_tier.qdrant/ missing")
    if issues:
        return False, "; ".join(issues)
    return True, f"cold {COLD_TIER.stat().st_size//1024}KB, warm {WARM_TIER_DIR.stat().st_size//1024}KB"


def probe_disk_space() -> tuple[bool, str]:
    usage = shutil.disk_usage(HERMES_HOME)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < DISK_FREE_MIN_GB:
        return False, f"disk free {free_gb:.1f}GB < threshold {DISK_FREE_MIN_GB}GB"
    return True, f"disk free {free_gb:.1f}GB"


def probe_state_heartbeat() -> tuple[bool, str]:
    """state.db-wal mtime should be recent if the gateway is actively serving."""
    if not WAL_FILE.exists():
        return False, "state.db-wal missing"
    age_min = (time.time() - WAL_FILE.stat().st_mtime) / 60
    if age_min > WAL_STALE_MINUTES:
        return False, f"state.db-wal stale ({age_min:.1f}m old, threshold {WAL_STALE_MINUTES}m)"
    return True, f"state.db-wal heartbeat {age_min:.1f}m old"


# ---------- MCP probes (NEW) ----------

def probe_mcp(name: str, server_cfg: dict) -> tuple[bool, str]:
    """Check whether an MCP server is reachable.

    Two-pronged:
      1. HTTP health endpoint (if defined) — verifies the underlying service
         (e.g. open_notebook MCP -> /api/notebooks at :5055)
      2. Binary probe (run with --help) — verifies the executable is sound
         (for stdio-based MCPs that only spawn when Hermes calls them)

    NOTE: stdio-based MCPs (voxcpm, liteparse, markitdown, tradingview*) are
    NOT long-lived daemons — they spawn per-call when Hermes invokes them.
    Checking for a running process gives false negatives (process absent
    because nothing called it lately is normal).  Probing the binary is the
    correct health signal.
    """
    info = MCP_DEFAULTS.get(name, {})
    health_url = info.get("health_url") or server_cfg.get("health_url")

    # Prong 1: HTTP
    if health_url:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as r:
                if r.status < 500:
                    return True, f"HTTP {r.status} from {health_url}"
        except Exception:
            pass  # fall through to binary probe

    # Prong 2: binary probe — run the MCP command with --help, expect exit 0
    # within a short timeout.  This proves the binary is intact + dependencies
    # resolve + it can initialize.
    cmd = server_cfg.get("command") if isinstance(server_cfg, dict) else None
    args = (server_cfg.get("args") or []) if isinstance(server_cfg, dict) else []
    if cmd:
        full = [str(cmd)] + [str(a) for a in args if a] + ["--help"]
        try:
            r = subprocess.run(
                full,
                capture_output=True, text=True, timeout=3,
                encoding="utf-8", errors="replace",
                env={**os.environ, **(server_cfg.get("env") or {})},
            )
            # Many MCPs print help to stderr or exit non-zero on --help;
            # treat "exit 0" or "exit with no error pattern" as healthy.
            if r.returncode == 0:
                return True, f"binary probe OK ({Path(str(cmd)).name})"
            # Some print --help to stderr with exit 1; that's still 'reachable'
            stderr_low = (r.stderr or "").lower()
            if "usage" in stderr_low or "help" in stderr_low or "options" in stderr_low:
                return True, f"binary probe OK ({Path(str(cmd)).name}, --help printed help text)"
        except subprocess.TimeoutExpired:
            # Stdio-based MCPs block on stdin waiting for JSON-RPC input and never
            # exit on --help.  A timeout means the binary started and initialized
            # correctly — treat as healthy.
            return True, f"binary probe OK ({Path(str(cmd)).name}, started OK — stdio MCP blocks on stdin)"
        except FileNotFoundError:
            return False, f"binary missing: {cmd}"
        except Exception as e:
            return False, f"binary probe failed: {type(e).__name__}: {e}"[:120]
        # Binary was found and ran without exception but exit ≠ 0 and no help text.
        out_snippet = ((r.stderr or "") + (r.stdout or ""))[:80].replace("\n", " ")
        return False, f"binary probe exit {r.returncode}: {out_snippet}"

    # Fallback: process check (for MCPs that DO run as daemons — none today)
    pid_hint = info.get("pid_hint")
    if not pid_hint:
        cmd_path = (server_cfg.get("command") or "") if isinstance(server_cfg, dict) else ""
        pid_hint = Path(cmd_path).stem if cmd_path else name
    try:
        import psutil
        known_hosts = {"python.exe", "python3.exe", "node.exe", "uvx.exe"}
        hint_low = pid_hint.lower()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name_low = (p.info['name'] or '').lower()
                if name_low not in known_hosts and hint_low not in name_low:
                    continue
                cmd_str = ' '.join(p.info['cmdline'] or []).lower()
                if hint_low in cmd_str:
                    return True, f"process match for {name} (PID={p.info['pid']})"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    return False, f"no health endpoint, binary probe, or process for {name}"


def probe_all_mcps(state: dict) -> dict[str, tuple[bool, str]]:
    """Probe every enabled MCP server. Returns {name: (ok, msg)}."""
    cfg = load_config()
    servers = cfg.get("mcp_servers") or {}
    results: dict[str, tuple[bool, str]] = {}
    for name, srv_cfg in servers.items():
        # Skip explicitly disabled
        if isinstance(srv_cfg, dict) and srv_cfg.get("enabled") is False:
            continue
        try:
            results[name] = probe_mcp(name, srv_cfg if isinstance(srv_cfg, dict) else {})
        except Exception as e:
            results[name] = (False, f"probe crashed: {type(e).__name__}: {e}"[:120])
    return results


# ---------- recovery actions ----------

def find_mcp_pids(pid_hint: str) -> list[int]:
    """Find PIDs whose command line contains pid_hint."""
    pids = []
    try:
        import psutil
        # Interpreter processes that host MCP scripts/modules
        known_hosts = {"python.exe", "python3.exe", "node.exe", "uvx.exe"}
        hint_low = pid_hint.lower()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name_low = (p.info['name'] or '').lower()
                # Accept Python/Node interpreter hosts OR processes whose own
                # exe name contains the hint (e.g. "research-project-mcp.exe"
                # when hint is "research-project-mcp").
                if name_low not in known_hosts and hint_low not in name_low:
                    continue
                cmd = ' '.join(p.info['cmdline'] or []).lower()
                if hint_low in cmd:
                    pids.append(p.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return pids


def kill_pids(pids: list[int]) -> int:
    """Force-kill a list of PIDs. Returns count actually killed."""
    killed = 0
    for pid in pids:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


def respawn_mcp(name: str, server_cfg: dict) -> tuple[bool, str]:
    """Respawn an MCP server by re-running its configured command."""
    cmd = server_cfg.get("command")
    args = server_cfg.get("args") or []
    env_extra = server_cfg.get("env") or {}
    if not cmd:
        return False, "no command in config"

    # Build env: inherit parent + add extras
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in env_extra.items()})

    # Resolve command path
    cmd_str = str(cmd)
    if cmd_str.endswith(".exe") and not os.path.isabs(cmd_str):
        # PATH lookup
        from shutil import which
        resolved = which(cmd_str)
        if resolved:
            cmd_str = resolved

    full_cmd = [cmd_str] + [str(a) for a in args if a]
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            full_cmd,
            env=env,
            cwd=str(HERMES_HOME),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, f"spawned: {' '.join(full_cmd)[:80]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def recover_mcp(name: str) -> tuple[bool, str]:
    """Full recovery cycle for an MCP: kill orphans + respawn from config."""
    cfg = load_config()
    srv = (cfg.get("mcp_servers") or {}).get(name) or {}
    if srv.get("no_respawn"):
        # stdio MCPs spawn per-call; only the binary probe matters.
        # Returning False here lets recovery_attempts exhaust quickly →
        # needs_attention, stopping the respawn churn.
        return False, "no_respawn=true — stdio MCP: binary probe is the health signal, no daemon to restart"
    info = MCP_DEFAULTS.get(name, {})
    pid_hint = info.get("pid_hint")
    if not pid_hint:
        # Derive from command exe stem so "source_credibility" (config key) doesn't
        # fail to match "source-credibility-mcp" (actual process name / cmdline token).
        cmd_path = srv.get("command") or ""
        pid_hint = Path(cmd_path).stem if cmd_path else name

    # Kill any orphans
    orphans = find_mcp_pids(pid_hint)
    killed = kill_pids(orphans) if orphans else 0
    # Brief pause so the port releases
    if killed:
        time.sleep(2)

    # Spawn fresh
    ok, msg = respawn_mcp(name, srv)
    if ok:
        return True, f"killed {killed}, {msg}"
    return False, f"killed {killed}, respawn failed: {msg}"


def kill_gateway_process() -> int:
    """Kill the running gateway process. Returns count of processes killed."""
    killed = 0
    # Try the PID file first (most precise — avoids killing unrelated pythonw procs)
    pid = None
    if PID_FILE.exists():
        try:
            pid_text = PID_FILE.read_text(encoding="utf-8").strip()
            for line in pid_text.splitlines():
                line = line.strip().lstrip("{").rstrip("}")
                if line.startswith('"pid"'):
                    pid = int(line.split(":", 1)[1].strip().rstrip(",").strip(' "'))
                    break
        except Exception:
            pass
    if pid:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                killed += 1
                log(f"kill_gateway: killed PID {pid}")
        except Exception:
            pass

    # Fallback: scan for pythonw processes running hermes gateway
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info["name"] or "").lower()
                if "pythonw" not in name and "python" not in name:
                    continue
                cmdline = " ".join(p.info["cmdline"] or []).lower()
                if "hermes_cli" in cmdline and "gateway" in cmdline:
                    p.kill()
                    killed += 1
                    log(f"kill_gateway: killed orphan PID {p.pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    return killed


def try_restart_gateway() -> tuple[bool, str]:
    """Kill existing gateway, start a fresh one, poll until ready (up to 25s)."""
    cmd_file = GATEWAY_SERVICE_DIR / "Hermes_Gateway.cmd"
    if not cmd_file.exists():
        return False, f"launcher missing: {cmd_file}"
    try:
        # Kill any existing gateway process before spawning a new one.
        # Without this, a slow-to-die gateway and the new one race for port 9119.
        killed = kill_gateway_process()
        if killed:
            time.sleep(2)  # brief pause so the port releases

        subprocess.Popen(
            ["cmd", "/c", str(cmd_file)],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
        )

        # Poll for gateway readiness.  Gateway startup (Python import + Telegram
        # connect) takes 6-8 seconds; a single 5s probe almost always fires too early
        # and produces a false "restart failed" that triggers another kill+restart cycle.
        last_msg = "no probe completed yet"
        for attempt in range(1, 6):   # up to 5 × 5s = 25s
            time.sleep(5)
            ok, last_msg = probe_gateway()
            if ok:
                return True, f"restarted (killed={killed}, attempt={attempt}); {last_msg}"

        return False, f"restarted but probe still failing after 25s: {last_msg}"
    except Exception as e:
        return False, f"restart failed: {e}"


def try_restart_docker_container(name: str) -> tuple[bool, str]:
    """Restart a single Docker container by name."""
    try:
        out = subprocess.run(
            ["docker", "restart", name],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return True, f"docker restart {name}: {out.stdout.strip()[:60]}"
        out2 = subprocess.run(
            ["docker", "start", name],
            capture_output=True, text=True, timeout=15,
        )
        if out2.returncode == 0:
            return True, f"docker start {name}: {out2.stdout.strip()[:60]}"
        return False, f"restart+start failed: {out.stderr.strip()[:80]}"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {name} timed out"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def try_restart_wsl_distro(name: str) -> tuple[bool, str]:
    """Restart a WSL distro (hermes-tools Stopped -> wake)."""
    try:
        subprocess.run(
            ["wsl", "--terminate", name],
            capture_output=True, text=True, timeout=15,
        )
        out = subprocess.run(
            ["wsl", "-d", name, "--", "/bin/true"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return True, f"wsl --terminate + boot {name} OK"
        return False, f"wsl boot failed: {out.stderr.strip()[:80]}"
    except subprocess.TimeoutExpired:
        return False, f"wsl restart {name} timed out"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


# ---------- main probe+recovery cycle ----------

def run_cycle() -> dict:
    """Run a single probe cycle with recovery. Returns summary dict."""
    state = load_rich_state()
    issues: list[str] = []
    recoveries: list[str] = []

    # Core probes (legacy set)
    core_probes = [
        ("gateway",      probe_gateway),
        ("docker",       probe_docker),
        ("wsl",          probe_wsl),
        ("honcho",       probe_honcho),
        ("open_notebook", probe_open_notebook),
        ("memory_tiers", probe_memory_tiers),
        ("disk_space",   probe_disk_space),
        ("heartbeat",    probe_state_heartbeat),
    ]

    for name, fn in core_probes:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"probe crashed: {type(e).__name__}: {e}"[:120]
        bump_fail(state, name, not ok)
        state.setdefault("services", {})[name] = {
            "ok": ok, "msg": msg, "last_check": now_iso(),
            "consecutive_failures": fail_count(state, name),
        }
        if not ok:
            issues.append(f"❌ {name}: {msg}")
            count = fail_count(state, name)
            # Recovery per-service
            if count >= RESTART_THRESHOLD and count % RESTART_THRESHOLD == 0:
                if name == "gateway":
                    ok2, rmsg = try_restart_gateway()
                    append_recovery_event(state, service=name, action="restart", ok=ok2, msg=rmsg)
                    if ok2:
                        recoveries.append(f"✅ gateway auto-restart: {rmsg}")
                        reset_fail(state, name)
                    else:
                        recoveries.append(f"⚠️ gateway restart failed: {rmsg}")
                        bump_recovery_attempts(state, name, True)
                elif name == "docker":
                    # Find which containers are down and restart them
                    down = []
                    try:
                        ps = subprocess.run(
                            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
                            capture_output=True, text=True, timeout=8,
                        )
                        for ln in ps.stdout.splitlines():
                            parts = ln.split("\t")
                            if len(parts) == 2 and parts[0] in EXPECTED_CONTAINERS:
                                if not parts[1].startswith("Up"):
                                    down.append(parts[0])
                    except Exception:
                        pass
                    if not down:
                        down = EXPECTED_CONTAINERS  # nuke-from-orbit fallback
                    for c in down:
                        ok2, rmsg = try_restart_docker_container(c)
                        append_recovery_event(state, service=f"docker:{c}", action="restart", ok=ok2, msg=rmsg)
                        if ok2:
                            recoveries.append(f"✅ docker restart {c}: {rmsg}")
                        else:
                            recoveries.append(f"⚠️ docker restart {c} failed: {rmsg}")
                            bump_recovery_attempts(state, f"docker:{c}", True)
                    time.sleep(3)
                    ok2, msg2 = probe_docker()
                    if ok2:
                        reset_fail(state, name)
                elif name == "wsl":
                    for d in WSL_MUST_BE_RUNNING:
                        ok2, rmsg = try_restart_wsl_distro(d)
                        append_recovery_event(state, service=f"wsl:{d}", action="restart", ok=ok2, msg=rmsg)
                        if ok2:
                            recoveries.append(f"✅ wsl restart {d}: {rmsg}")
                            reset_fail(state, "wsl")
                        else:
                            recoveries.append(f"⚠️ wsl restart {d} failed: {rmsg}")
                            bump_recovery_attempts(state, f"wsl:{d}", True)

    # MCP probes (new)
    mcp_results = probe_all_mcps(state)
    for mcp_name, (ok, msg) in mcp_results.items():
        # Use prefixed key so MCP failures don't share buckets with core
        key = f"mcp:{mcp_name}"
        bump_fail(state, key, not ok)
        state.setdefault("services", {})[key] = {
            "ok": ok, "msg": msg, "last_check": now_iso(),
            "consecutive_failures": fail_count(state, key),
            "service": mcp_name,
        }
        if not ok:
            issues.append(f"❌ {key}: {msg}")
            count = fail_count(state, key)
            if count >= RESTART_THRESHOLD and count % RESTART_THRESHOLD == 0:
                ok2, rmsg = recover_mcp(mcp_name)
                append_recovery_event(state, service=mcp_name, action="recover", ok=ok2, msg=rmsg)
                if ok2:
                    recoveries.append(f"✅ MCP {mcp_name} recovered: {rmsg}")
                    reset_fail(state, key)
                    bump_recovery_attempts(state, key, False)
                else:
                    recoveries.append(f"⚠️ MCP {mcp_name} recovery failed: {rmsg}")
                    bump_recovery_attempts(state, key, True)
                    if recovery_attempts(state, key) >= RECOVERY_EXHAUSTED:
                        # mark needs_attention
                        state.setdefault("services", {})[key]["status"] = "needs_attention"
                        recoveries.append(f"🚨 MCP {mcp_name} marked needs_attention after {RECOVERY_EXHAUSTED} failed recoveries")

    state["last_run"] = now_iso()

    # Compute fingerprint of currently-failing services (sorted, comma-joined).
    #
    # NOISE_KEYS are probes that fail under normal conditions (e.g. heartbeat
    # fails whenever no user is interacting for >30 min). They are still
    # recorded in state["services"] for the dashboard, but they NEVER
    # trigger Telegram alerts — neither as failures nor as recoveries.
    # This eliminates the heartbeat oscillation spam.
    NOISE_KEYS = {"heartbeat"}
    failed_keys = sorted(
        k for k, v in state.get("services", {}).items()
        if not v.get("ok", True)
    )
    actionable_keys = [k for k in failed_keys if k not in NOISE_KEYS]
    current_fp = ",".join(actionable_keys)
    last_fp = state.get("alert_fingerprint", "")

    new_failure = current_fp and current_fp != last_fp
    recovered = last_fp and not current_fp

    # Always persist state (so dashboard can read fresh data)
    save_rich_state(state)

    # ALSO write the legacy state file (for /api/health/deep compatibility)
    legacy = {
        "consecutive_failures": state.get("consecutive_failures", {}),
        "last_run": state["last_run"],
        "last_clean": state.get("last_clean"),
        "last_alert": state.get("last_alert"),
        "alert_fingerprint": current_fp,
        "version": 2,
    }
    try:
        LEGACY_STATE.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_STATE.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    except Exception:
        pass

    if not new_failure and not recovered and not recoveries:
        state["last_clean"] = now_iso()
        save_rich_state(state)
        log("silent (all probes passing)")
        return {"silent": True, "issues": [], "recoveries": []}

    if new_failure or recovered or recoveries:
        # Suppress transient single-cycle flaps. Only alert when something has
        # actually reached ALERT_THRESHOLD consecutive failures — that's the
        # "real issue, not a blip" signal. Single-cycle failures (e.g. one
        # gateway timeout before self-recovery) no longer spam Telegram.
        sustained_failure = any(
            fail_count(state, k) >= ALERT_THRESHOLD
            for k in actionable_keys
        )

        if new_failure and not sustained_failure:
            log(f"transient flap suppressed: fp={current_fp} (no probe at threshold yet)")
            new_failure = False  # don't alert on single-cycle blips
        elif recovered and last_fp:
            # Only "Recovered" matters if the prior issue was sustained.
            # Single-cycle recoveries of noise probes are invisible.
            prior_keys = set(last_fp.split(",")) if last_fp else set()
            if not prior_keys & set(actionable_keys):
                # The fingerprint we're recovering from had no actionable
                # components (it was only noise) — suppress recovery notice.
                log(f"noise-only recovery suppressed: was {last_fp}")
                recovered = False

        # Build Telegram alert (only on real state changes — gated by threshold)
        if new_failure or recovered:
            lines = ["🐕 *Hermes Watchdog — state change*", ""]
            if recovered:
                lines.append(f"*Recovered:* was `{last_fp}`, now all probes passing.")
                lines.append("")
            if recoveries:
                lines.append("*Recoveries this run:*")
                lines.extend(recoveries)
                lines.append("")
            if issues:
                lines.append("*Current issues:*")
                # Filter out noise probes from the issues list in alerts too
                actionable_issues = [i for i in issues[:10]
                                    if not any(i.startswith(f"❌ {n}:") for n in NOISE_KEYS)]
                lines.extend(actionable_issues)
                lines.append("")
            threshold_hit = [
                k for k in actionable_keys
                if fail_count(state, k) >= ALERT_THRESHOLD
            ]
            if threshold_hit:
                lines.append(f"*At threshold ({ALERT_THRESHOLD}+ consecutive):*")
                lines.append(", ".join(f"`{k}`" for k in threshold_hit))
                lines.append("")
            lines.append(f"_host: Z13 · run: {now_iso()}_")
            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:3950] + "\n\n_…truncated_"
            send_telegram(msg)
            state["last_alert"] = now_iso()
            log(f"alert sent (fp={current_fp[:80]})", level="WARN")
        elif recoveries:
            # No alert — silent recovery (per design)
            log(f"silent recovery: {' | '.join(recoveries)[:200]}")

        state["alert_fingerprint"] = current_fp
        save_rich_state(state)

    return {
        "silent": False,
        "issues": issues,
        "recoveries": recoveries,
        "fingerprint": current_fp,
    }


# ---------- CLI ----------

def cmd_once() -> int:
    """Run a single probe+recovery cycle."""
    summary = run_cycle()
    if summary["silent"]:
        print("[SILENT]")
        return 0
    issue_list = summary.get("issues", [])
    recovery_list = summary.get("recoveries", [])
    print(f"issues: {len(issue_list)}, recoveries: {len(recovery_list)}")
    for r in recovery_list:
        print(f"  {r}")
    for i in issue_list:
        print(f"  {i}")
    # Always exit 0.  The watchdog manages its own Telegram alerts (fingerprint-deduped).
    # Exiting 1 on issues causes the cron runner to send a duplicate
    # "Script exited with code 1" message to Telegram on top of the watchdog's alert.
    return 0


def cmd_daemon(interval: int) -> int:
    """Run continuously with a sleep loop. Handles SIGINT/SIGTERM gracefully."""
    log(f"daemon started (interval={interval}s)")
    stop = False

    def _stop(signum, frame):
        nonlocal stop
        log(f"received signal {signum}, stopping")
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while not stop:
        try:
            run_cycle()
        except Exception as e:
            log(f"cycle crashed: {type(e).__name__}: {e}", level="ERROR")
        # Sleep in small chunks so SIGTERM is responsive
        for _ in range(interval):
            if stop:
                break
            time.sleep(1)
    log("daemon stopped")
    return 0


def cmd_status() -> int:
    """Print current state and exit."""
    state = load_rich_state()
    print(f"version:        {state.get('version')}")
    print(f"started_at:     {state.get('started_at')}")
    print(f"last_run:       {state.get('last_run')}")
    print(f"last_clean:     {state.get('last_clean')}")
    print(f"last_alert:     {state.get('last_alert')}")
    print(f"services:       {len(state.get('services', {}))}")
    print(f"recovery_hist:  {len(state.get('recovery_history', []))} events")
    print()
    print("Services:")
    for name in sorted(state.get("services", {})):
        s = state["services"][name]
        flag = "OK" if s.get("ok") else "FAIL"
        cf = s.get("consecutive_failures", 0)
        print(f"  [{flag:4s}] {name:30s} cf={cf}  {s.get('msg', '')[:80]}")
    print()
    print(f"Dependency map: {len(DEPENDENCY_MAP)} services declared")
    return 0


def cmd_history(n: int) -> int:
    """Print last N recovery events."""
    state = load_rich_state()
    hist = state.get("recovery_history", [])
    for ev in hist[-n:]:
        flag = "OK" if ev.get("ok") else "FAIL"
        print(f"  [{flag}] {ev.get('ts')} {ev.get('service'):20s} {ev.get('action'):10s} {ev.get('msg', '')[:80]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes self-healing watchdog")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit (default)")
    parser.add_argument("--daemon", action="store_true", help="run continuously")
    parser.add_argument("--status", action="store_true", help="print state and exit")
    parser.add_argument("--history", type=int, metavar="N", help="print last N recovery events")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("HERMES_WATCHDOG_INTERVAL", "60")),
                        help="daemon sleep interval (seconds, default 60)")
    args = parser.parse_args()

    if args.daemon:
        return cmd_daemon(args.interval)
    if args.status:
        return cmd_status()
    if args.history is not None:
        return cmd_history(args.history)
    return cmd_once()


if __name__ == "__main__":
    sys.exit(main())