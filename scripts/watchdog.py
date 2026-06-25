#!/usr/bin/env python3
"""Hermes watchdog — Option A (light health probe).

Runs every 10 min via cron. Probes:
- Gateway HTTP :9119 + PID liveness + HTML sanity
- Cron job last-run health (via `hermes cron list`)
- MCP server status (via `hermes mcp list`, default profile)
- Memory tier files exist + recent activity (cold_tier.db, warm_tier.qdrant)
- Honcho background service reachable (known-failing in errors.log)
- state.db-wal mtime (heartbeat — should be recent if agent is alive)
- Disk free space

On failure: tries restart where possible (gateway), alerts via Telegram on
>=2 consecutive failures per check. Tracks state in cron/output/watchdog/state.json.

Output: Telegram alert on issues, [SILENT] when clean (gateway suppresses delivery).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
GATEWAY_URL = "http://127.0.0.1:9119/"
PID_FILE = HERMES_HOME / "gateway.pid"
STATE_FILE = HERMES_HOME / "cron/output/watchdog/state.json"
ERRORS_LOG = HERMES_HOME / "logs/errors.log"
WAL_FILE = HERMES_HOME / "state.db-wal"
COLD_TIER = HERMES_HOME / "cold_tier.db"
WARM_TIER_DIR = HERMES_HOME / "warm_tier.qdrant"
STATE_DB = HERMES_HOME / "state.db"
GATEWAY_LOG = HERMES_HOME / "logs/gateway.log"
ALERT_THRESHOLD = 2          # consecutive failures before alerting
RESTART_THRESHOLD = 3        # consecutive failures before auto-restart (transient-blip tolerance)
DISK_FREE_MIN_GB = 10        # alert if less than this
WAL_STALE_MINUTES = 30       # state.db-wal must be touched within this many min if gateway is active
HONCHO_PORT = 8000           # honcho default port; if different in env, update
TIMEOUT = 8                  # seconds per probe

# Expected Docker containers (must all be in `running` state for the probe to pass).
# Honcho stack is started via `start-honcho.cmd` in C:\honcho — Docker Compose
# appends `-1` to service names, so the actual container names are suffixed.
# Speaches and qdrant-research are standalone; keep bare names.
EXPECTED_CONTAINERS = [
    "honcho-api-1",
    "honcho-database-1",
    "honcho-redis-1",
    "honcho-deriver-1",
    "speaches",
    "qdrant-research",
]
# hermes-tools can be Stopped — but we try to keep it Running so cron jobs work.
# docker-desktop is auto-managed by Docker Desktop; if missing, we alert but
# don't try to restart (Docker owns that lifecycle).
EXPECTED_WSL_DISTROS = ["docker-desktop", "hermes-tools"]
WSL_MUST_BE_RUNNING = ["hermes-tools"]  # subset that watchdog will try to boot

# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"consecutive_failures": {}, "last_clean": None, "last_alert": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fail_count(state: dict, key: str) -> int:
    return state.get("consecutive_failures", {}).get(key, 0)


def bump_fail(state: dict, key: str, failed: bool) -> None:
    cf = state.setdefault("consecutive_failures", {})
    # Increment on fail, reset to 0 on success.
    cf[key] = (cf.get(key, 0) + 1) if failed else 0


def reset_via_success(state: dict, key: str) -> None:
    """Reset a counter after an externally-initiated recovery (e.g. auto-restart)."""
    state.setdefault("consecutive_failures", {})[key] = 0


def send_telegram(text: str) -> None:
    """Fire-and-forget Telegram send via hermes CLI. Best-effort — never raises."""
    try:
        subprocess.run(
            ["hermes", "send", "--to", "telegram", text],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


# ---------- probes ----------

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
        # PID alive check
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


def probe_cron_health() -> tuple[bool, str]:
    """Run `hermes cron list`, parse for failed/stale jobs.

    Excludes the watchdog's own row from failure counting — when the watchdog
    successfully sends a Telegram alert, it exits with code 1, which the cron
    framework records as "error: Script exited with code 1". That's the
    watchdog doing its job (alert sent), not a failure. Without this filter
    the watchdog would flag itself as a failed cron job every cycle.
    """
    try:
        out = subprocess.run(
            ["hermes", "cron", "list"], capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return False, f"hermes cron list failed: {e}"
    if out.returncode != 0:
        return False, f"hermes cron list exit {out.returncode}: {out.stderr[:100]}"
    text = out.stdout
    failed_jobs = []
    current_job = None
    for line in text.splitlines():
        m_name = re.search(r"Name:\s+(\S+)", line)
        if m_name:
            current_job = m_name.group(1)
        # Two observed formats for the Last run line:
        #   "    Last run:  2026-06-17T13:32:44.597101-07:00  ok"
        #   "    Last run:  2026-06-17T13:31:17.358605-07:00  error: Script exited with code 1"
        # The regex captures the status as either "ok"/"scheduled" (single word)
        # or "error:" (followed by description). Detect by leading substring.
        m_last = re.search(r"Last run:\s+(\S+)\s+(.+)", line)
        if m_last and current_job:
            ts = m_last.group(1)
            rest = m_last.group(2).strip()
            # Status is either first word (ok, scheduled) or starts with "error:"
            if rest.startswith("error"):
                status_word = "error"
            else:
                status_word = rest.split()[0] if rest.split() else ""
            # Skip watchdog-self: exit 1 from watchdog means "alert sent" = success.
            if current_job == "watchdog-health-probe" and status_word == "error":
                current_job = None
                continue
            # Skip stall-detector: exit 1 means "alert sent" = success (same pattern).
            if current_job == "stall-detector" and status_word == "error":
                current_job = None
                continue
            if status_word not in ("ok", "scheduled"):
                failed_jobs.append(f"{current_job}: {status_word} @ {ts}")
            current_job = None
    if failed_jobs:
        return False, f"{len(failed_jobs)} failed cron job(s): " + ", ".join(failed_jobs[:3])
    return True, "all cron jobs ok or scheduled"


def probe_mcp_servers() -> tuple[bool, str]:
    """Check `hermes mcp list` for any disabled/dead servers (default profile)."""
    try:
        out = subprocess.run(
            ["hermes", "mcp", "list"], capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        return False, f"hermes mcp list failed: {e}"
    if out.returncode != 0:
        return False, f"hermes mcp list exit {out.returncode}"
    text = out.stdout
    # Format: "Name ... Status" with rows like "  tradingview ... ✓ enabled"
    bad = []
    servers = []
    for line in text.splitlines():
        if "✓" in line or "✗" in line or "enabled" in line or "disabled" in line:
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                servers.append(name)
                if "✗" in line or "disabled" in line or "error" in line.lower():
                    bad.append(name)
    if not servers:
        # No MCP servers at all is fine — not an error
        return True, "no MCP servers configured (ok)"
    if bad:
        return False, f"MCP servers unhealthy: {', '.join(bad)}"
    return True, f"{len(servers)} MCP server(s) enabled"


def probe_memory_tiers() -> tuple[bool, str]:
    issues = []
    if not COLD_TIER.exists():
        issues.append("cold_tier.db missing")
    if not WARM_TIER_DIR.exists():
        issues.append("warm_tier.qdrant/ missing")
    if issues:
        return False, "; ".join(issues)
    return True, f"cold {COLD_TIER.stat().st_size//1024}KB, warm {WARM_TIER_DIR.stat().st_size//1024}KB"


def probe_honcho() -> tuple[bool, str]:
    """Honcho is the background memory service. Known to fail in errors.log with
    WinError 10061 — flag it as a real issue so we don't keep silently swallowing it.

    Retry up to 3 times with short backoff (Honcho API occasionally drops the
    connection mid-request even when healthy — empty TCP reply). A single
    failed probe doesn't constitute an outage.
    """
    last_err = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{HONCHO_PORT}/health", timeout=3
            ) as r:
                body = r.read().decode("utf-8", errors="replace")
                if r.status == 200 and "ok" in body.lower():
                    if attempt > 1:
                        return True, f"Honcho /health OK after {attempt} attempts ({body[:40]})"
                    return True, f"Honcho /health OK ({body[:40]})"
                last_err = f"Honcho HTTP {r.status} body={body[:80]}"
        except urllib.error.URLError as e:
            last_err = f"Honcho unreachable on :{HONCHO_PORT} (attempt {attempt}/3: {e.reason})"
        except Exception as e:
            last_err = f"Honcho probe failed (attempt {attempt}/3): {e}"[:80]
        if attempt < 3:
            time.sleep(1.0)  # short backoff between retries
    return False, last_err


def probe_docker() -> tuple[bool, str]:
    """Docker daemon + all expected containers in running state.

    Three sub-checks: daemon reachable, daemon response time, all expected
    containers present and 'Up'. Returns structured summary so failures
    point at the actual problem.
    """
    try:
        # Daemon liveness + latency
        t0 = time.time()
        out = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=8,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        if out.returncode != 0:
            return False, f"docker daemon unreachable: {out.stderr.strip()[:100]}"
        # WSL→host docker bridge can be slow on cold path; 5s is normal
        if elapsed_ms > 5000:
            return False, f"docker daemon slow ({elapsed_ms}ms) — possible WSL/Desktop issue"
        daemon_ver = out.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "docker info timed out (daemon hung?)"
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except Exception as e:
        return False, f"docker probe failed: {type(e).__name__}: {e}"[:120]

    # Container state check
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
                  if c in states and states[c].lower() != "running"]  # noqa: E501
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
    """WSL distros must all be registered; hermes-tools must additionally be live.

    Distro 'docker-desktop' is auto-managed by Docker; just needs to be
    present. 'hermes-tools' is our Linux toolchain — must be live so
    scheduled tasks (e.g. backup-honcho-daily) can invoke it.

    Two layers of evidence:
      1. PASSIVE: `wsl -l -v` shows the registry state. Note: wsl.exe can
         transiently report 'Stopped' during VM boot cycles even though the
         distro is reachable. We treat the passive state as advisory only.
      2. ACTIVE: `wsl -d hermes-tools -- echo OK` actually executes a
         command inside the distro. If it returns 0, the distro is live
         regardless of what the registry says.

    Decision: pass if either layer is healthy. Fail only if both layers
    indicate problems (registry says Stopped AND active probe times out).
    This eliminates false-positives during boot.

    Keep-alive ping runs on every probe cycle regardless of result, so a
    Stopped distro gets woken up immediately instead of waiting 10 min.
    """
    # Always do keep-alive ping first (independent of probe outcome).
    try:
        subprocess.run(
            ["wsl", "-d", "hermes-tools", "--", "/bin/true"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    # Layer 1: active probe (more reliable than registry state)
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

    # Layer 2: passive registry state (advisory)
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
        pass  # registry read failure is non-fatal if active probe worked

    # Check expected distros are registered (use registry for this)
    missing = [d for d in EXPECTED_WSL_DISTROS if d not in registry]
    if missing:
        return False, f"WSL distros missing from registry: {missing}"

    hermes_state = registry.get("hermes-tools", "?")
    if active_ok:
        return True, f"WSL: docker-desktop={registry.get('docker-desktop','?')}, hermes-tools={hermes_state} (active OK)"
    # Active probe failed — fail only if registry also confirms Stopped
    if hermes_state == "Stopped":
        return False, f"hermes-tools Stopped AND echo failed: {active_msg}"
    # Registry says Running but active probe failed — likely booting
    return False, f"hermes-tools={hermes_state} but echo failed: {active_msg}"


def probe_state_heartbeat() -> tuple[bool, str]:
    """state.db-wal mtime should be recent if the gateway is actively serving."""
    if not WAL_FILE.exists():
        return False, "state.db-wal missing"
    age_min = (time.time() - WAL_FILE.stat().st_mtime) / 60
    if age_min > WAL_STALE_MINUTES:
        return False, f"state.db-wal stale ({age_min:.1f}m old, threshold {WAL_STALE_MINUTES}m)"
    return True, f"state.db-wal heartbeat {age_min:.1f}m old"


def probe_disk_space() -> tuple[bool, str]:
    import shutil
    usage = shutil.disk_usage(HERMES_HOME)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < DISK_FREE_MIN_GB:
        return False, f"disk free {free_gb:.1f}GB < threshold {DISK_FREE_MIN_GB}GB"
    return True, f"disk free {free_gb:.1f}GB"


# ---------- recovery ----------

def try_restart_mcp(profile_path: Path, mcp_name: str) -> tuple[bool, str]:
    """Attempt to restart a specific MCP server by re-running its command.

    Reads the profile's mcp_servers block, kills any matching npx/node process
    for that server, and respawns it. Caller should verify it's safe to
    restart (no active sessions).
    """
    import yaml as _yaml
    if not profile_path.exists():
        return False, f"profile config missing: {profile_path}"
    try:
        cfg = _yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return False, f"profile yaml unreadable: {e}"
    servers = cfg.get("mcp_servers") or {}
    srv = servers.get(mcp_name)
    if not srv:
        return False, f"mcp_servers.{mcp_name} not in config"
    cmd = srv.get("command")
    args = srv.get("args") or []
    if not cmd:
        return False, "no command in config"
    # Kill any existing npx/node for this server (heuristic: kill processes whose
    # argv contains the mcp_name OR a unique arg). Conservative: we kill only
    # processes whose CWD or argv mentions the package.
    try:
        # Find processes matching this server (heuristic: name in cmdline)
        out = subprocess.run(
            ["wmic", "process", "where",
             f"name='node.exe' or name='npx.exe'",
             "get", "ProcessId,CommandLine", "/format:CSV"],
            capture_output=True, text=True, timeout=10,
        )
        killed = 0
        for line in out.stdout.splitlines()[1:]:
            if not line.strip():
                continue
            try:
                pid_str, _, cmdline = line.partition(",")
                # cmdline has the rest (may contain commas)
                cmdline = line[len(pid_str) + 1:].strip('"')
                if mcp_name in cmdline.lower() or any(a.lower() in cmdline.lower() for a in args if isinstance(a, str)):
                    pid = int(pid_str)
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5)
                    killed += 1
            except (ValueError, IndexError):
                continue
        # Spawn the replacement (detached). Use CREATE_NEW_PROCESS_GROUP so it
        # survives the watchdog exiting.
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        full_cmd = [cmd] + [str(a) for a in args]
        subprocess.Popen(
            full_cmd,
            cwd=str(profile_path.parent),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, f"killed {killed} matching process(es), spawned fresh"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def count_active_sessions() -> int:
    """Number of sessions that look actively in-progress right now."""
    import sqlite3 as _sqlite3
    db = HERMES_HOME / "state.db"
    if not db.exists():
        return 0
    try:
        con = _sqlite3.connect(str(db), timeout=3)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL")
        n = cur.fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


def try_restart_gateway() -> tuple[bool, str]:
    """Best-effort gateway restart. Wraps the existing service launcher."""
    cmd_file = HERMES_HOME / "gateway-service" / "Hermes_Gateway.cmd"
    if not cmd_file.exists():
        return False, f"launcher missing: {cmd_file}"
    try:
        # Start detached via cmd; gateway writes its own pid file when ready.
        subprocess.Popen(
            ["cmd", "/c", str(cmd_file)],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
        )
        # Give it a moment to come up
        time.sleep(5)
        ok, msg = probe_gateway()
        if ok:
            return True, f"restarted; {msg}"
        return False, f"restarted but probe still failing: {msg}"
    except Exception as e:
        return False, f"restart failed: {e}"


def try_restart_docker_container(name: str) -> tuple[bool, str]:
    """Restart a single Docker container by name.

    Uses `docker restart` which is atomic — preserves the image, volumes,
    and any restart policy. Falls back to `docker start` if the container
    is in 'Exited' state.
    """
    try:
        out = subprocess.run(
            ["docker", "restart", name],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return True, f"docker restart {name}: {out.stdout.strip()[:60]}"
        # Fallback: maybe it was created but never started
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
    """Restart a WSL distro (useful when 'hermes-tools' is in Stopped state).

    Uses `wsl --terminate` then `wsl -d <name> -- true` to boot it back up.
    """
    try:
        subprocess.run(
            ["wsl", "--terminate", name],
            capture_output=True, text=True, timeout=15,
        )
        # Boot it by running a no-op command
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


# ---------- main ----------

def main() -> int:
    state = load_state()
    issues: list[str] = []
    recoveries: list[str] = []

    # Run all probes
    probes = [
        ("gateway",      probe_gateway),
        ("cron_health",  probe_cron_health),
        ("mcp_servers",  probe_mcp_servers),
        ("memory_tiers", probe_memory_tiers),
        ("honcho",       probe_honcho),
        ("docker",       probe_docker),
        ("wsl",          probe_wsl),
        ("heartbeat",    probe_state_heartbeat),
        ("disk_space",   probe_disk_space),
    ]

    for name, fn in probes:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"probe crashed: {type(e).__name__}: {e}"[:120]
        bump_fail(state, name, not ok)
        if not ok:
            entry = f"❌ {name}: {msg}"
            # All auto-restarts now require RESTART_THRESHOLD (3) consecutive failures
            # so transient blips (brief docker timeout, WSL wake-up) don't trigger
            # cascading restarts. ALERT_THRESHOLD (2) still fires the Telegram alert.
            count = fail_count(state, name)
            # Gateway: attempt auto-restart after RESTART_THRESHOLD consecutive failures
            if name == "gateway" and count >= RESTART_THRESHOLD and count % RESTART_THRESHOLD == 0:
                restarted, rmsg = try_restart_gateway()
                if restarted:
                    recoveries.append(f"✅ gateway auto-restart: {rmsg}")
                    reset_via_success(state, name)
                else:
                    recoveries.append(f"⚠️ gateway restart attempt failed: {rmsg}")
            # Docker: identify which containers are down and restart them
            elif name == "docker" and count >= RESTART_THRESHOLD and count % RESTART_THRESHOLD == 0:
                # Parse the failure message to find specific containers
                down_containers = []
                for line in (msg.split(":", 1)[-1] if ":" in msg else msg).split(","):
                    cand = line.strip().strip("[]").strip()
                    if cand in EXPECTED_CONTAINERS:
                        down_containers.append(cand)
                # If we couldn't parse specific containers, restart all that are not Up
                if not down_containers:
                    try:
                        ps_out = subprocess.run(
                            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
                            capture_output=True, text=True, timeout=8,
                        )
                        for ln in ps_out.stdout.splitlines():
                            parts = ln.split("\t")
                            if len(parts) == 2 and parts[0] in EXPECTED_CONTAINERS:
                                if not parts[1].startswith("Up"):
                                    down_containers.append(parts[0])
                    except Exception:
                        pass
                for c in down_containers:
                    restarted, rmsg = try_restart_docker_container(c)
                    if restarted:
                        recoveries.append(f"✅ docker restart {c}: {rmsg}")
                    else:
                        recoveries.append(f"⚠️ docker restart {c} failed: {rmsg}")
                # If honcho stack was down, try bringing the whole stack up
                honcho_down = any(c.startswith("honcho-") for c in down_containers)
                if honcho_down:
                    try:
                        subprocess.run(
                            ["cmd", "/c", "start-honcho.cmd"],
                            cwd="C:\\honcho",
                            capture_output=True, text=True, timeout=60,
                        )
                        recoveries.append("🔄 invoked start-honcho.cmd (full stack)")
                    except Exception as e:
                        recoveries.append(f"⚠️ start-honcho.cmd failed: {e}")
                # Re-probe docker to see if recoveries worked
                time.sleep(3)
                ok2, msg2 = probe_docker()
                if ok2:
                    reset_via_success(state, name)
                else:
                    recoveries.append(f"⚠️ docker probe still failing: {msg2}")
            # WSL: restart any non-running expected distros (only hermes-tools;
            # docker-desktop is owned by Docker Desktop itself, never restart it).
            elif name == "wsl" and count >= RESTART_THRESHOLD and count % RESTART_THRESHOLD == 0:
                for d in WSL_MUST_BE_RUNNING:  # only restart what we manage
                    restarted, rmsg = try_restart_wsl_distro(d)
                    if restarted:
                        recoveries.append(f"✅ wsl restart {d}: {rmsg}")
                    else:
                        recoveries.append(f"⚠️ wsl restart {d} failed: {rmsg}")
                ok2, msg2 = probe_wsl()
                if ok2:
                    reset_via_success(state, name)
            if fail_count(state, name) > 0:
                issues.append(entry)

    state["last_run"] = now_iso()

    # Compute current failure fingerprint: sorted comma-joined probe names.
    failed_names = sorted(name for name, _ in probes if fail_count(state, name) > 0)
    current_fp = ",".join(failed_names)
    last_fp = state.get("alert_fingerprint", "")

    # Suppress if fingerprint unchanged since last alert (avoid spam on persistent issues).
    # New alerts fire when:
    #   - a previously-passing probe is now failing (fp grew)
    #   - a previously-failing probe is now passing (fp shrank) — send "recovered" note
    #   - the failing set changed composition
    new_failure = current_fp and current_fp != last_fp
    recovered = last_fp and not current_fp

    if not new_failure and not recovered and not recoveries:
        # Fingerprint unchanged and nothing recovered this run — silent.
        state["last_clean"] = now_iso()
        save_state(state)
        print("[SILENT]")
        return 0

    # Build alert (Telegram-friendly, under 4000 chars)
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
        lines.extend(issues)
        lines.append("")
    threshold_hit = [n for n in failed_names if fail_count(state, n) >= ALERT_THRESHOLD]
    if threshold_hit:
        lines.append(f"*At threshold ({ALERT_THRESHOLD}+ consecutive):*")
        lines.append(", ".join(f"`{n}`" for n in threshold_hit))
        lines.append("")
    lines.append(f"_host: Z13 · run: {now_iso()}_")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_…truncated_"

    send_telegram(msg)
    state["last_alert"] = now_iso()
    state["alert_fingerprint"] = current_fp
    save_state(state)
    print("alert sent")
    return 1


if __name__ == "__main__":
    sys.exit(main())
