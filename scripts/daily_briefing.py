"""daily_briefing.py — morning briefing aggregator.

Pulls tasks + calendar + Gmail + research follow-ups + watchdog health, then
either posts the digest to Telegram (default for cron) or prints it (manual
runs). Idempotent — running multiple times in a day is safe; the snapshot
gets overwritten.

Usage:
    python scripts/daily_briefing.py            # prints to stdout
    python scripts/daily_briefing.py --post     # posts to Telegram home
    python scripts/daily_briefing.py --json     # writes data/daily_briefing.json

Cron:
    0 7 * * *  python scripts/daily_briefing.py --post

State files:
    C:\\Data\\Hermes_0.17.0\\data\\daily_briefing.json   (latest snapshot for dashboard)
    C:\\Data\\Hermes_0.17.0\\health\\daily_briefing.log  (run history, last 30 entries)

The script never restarts the gateway. It calls existing APIs on
http://127.0.0.1:9119 + reads on-disk data only. Safe to run alongside the
agent; it cannot deadlock with cron jobs that share the same dashboards.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
DASHBOARD_BASE = os.environ.get("HERMES_DASHBOARD_BASE", "http://127.0.0.1:9119")
SNAPSHOT_PATH = HERMES_HOME / "data" / "daily_briefing.json"
LOG_PATH = HERMES_HOME / "health" / "daily_briefing.log"
_SESSION_TOKEN_CACHE: str | None = None


def _bootstrap_token() -> str | None:
    """Grab the dashboard session token from the SPA's index page.

    The SPA HTML embeds the token in an inline <script>. Fetching /
    is cheap (~10KB) and avoids needing a separate auth handshake.
    """
    global _SESSION_TOKEN_CACHE
    if _SESSION_TOKEN_CACHE:
        return _SESSION_TOKEN_CACHE
    try:
        req = urllib.request.Request(f"{DASHBOARD_BASE}/")
        with urllib.request.urlopen(req, timeout=5) as r:
            html = r.read().decode("utf-8", errors="ignore")
        import re
        m = re.search(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
        if m:
            _SESSION_TOKEN_CACHE = m.group(1)
            return _SESSION_TOKEN_CACHE
    except Exception as e:
        _log(f"token bootstrap failed: {type(e).__name__}: {e}")
    return None


def _api(path: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET a public-or-token-gated dashboard endpoint and return parsed JSON.

    All endpoints used here are in the public allowlist OR the session
    token grants access. Endpoints known to 500: /api/watchdog/state
    (pre-existing web_server.py NameError) — we fall back to
    /api/health/deep for that.
    """
    token = _bootstrap_token()
    req = urllib.request.Request(f"{DASHBOARD_BASE}{path}")
    if token:
        req.add_header("X-Hermes-Session-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "_status": e.code, "_path": path}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "_path": path}


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_dt.datetime.now().isoformat()}  {msg}"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # Truncate to last 30 entries
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > 30:
            LOG_PATH.write_text("\n".join(lines[-30:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _rel_time(iso: str) -> str:
    if not iso:
        return "—"
    try:
        d = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = _dt.datetime.now(_dt.timezone.utc) - d
        mins = int(delta.total_seconds() / 60)
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        return f"{hrs // 24}d ago"
    except Exception:
        return iso[:10] if iso else "—"


def _format_event_time(s: dict[str, Any] | None) -> str:
    if not s:
        return ""
    if s.get("dateTime"):
        try:
            d = _dt.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00"))
            today = _dt.datetime.now().date()
            if d.date() == today:
                return f"today {d.strftime('%H:%M')}"
            return d.strftime("%a %H:%M")
        except Exception:
            return s["dateTime"]
    if s.get("date"):
        return s["date"]
    return ""


def _build_briefing() -> dict[str, Any]:
    """Fetch all sources in parallel-friendly sequential order. Returns a dict
    ready for both JSON serialization and Telegram formatting.
    """
    personal = _api("/api/personal/snapshot")
    watchdog = _api("/api/watchdog/state")
    if watchdog.get("_status") == 500:
        watchdog = _api("/api/health/deep")
        watchdog["_fallback"] = "watchdog state endpoint 500s — using health/deep"
    research = _api("/api/research/projects?status=active")
    syntheses = _api("/api/research/syntheses/recent?limit=3")

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "personal": personal,
        "watchdog": watchdog,
        "research": research,
        "recent_syntheses": syntheses,
    }


def _format_telegram(b: dict[str, Any]) -> str:
    """Render the briefing as a Telegram-friendly message.

    Sections are compact, emoji-coded, and capped at ~4000 chars (Telegram's
    limit is 4096). If nothing's actionable, the message says so — no padding.
    """
    lines: list[str] = []
    add = lines.append

    add("☀️ *Hermes morning briefing*")
    add(f"_{_dt.datetime.now().strftime('%A %b %d, %H:%M')}_")
    add("")

    # ---- Tasks ----
    t = (b.get("personal") or {}).get("tasks") or {}
    over = t.get("overdue") or []
    due = t.get("due_24h") or []
    if t.get("status") == "degraded":
        add("📋 *Tasks*: ⚠ degraded — " + (t.get("error") or "unknown"))
    elif over or due:
        add("📋 *Tasks* — action needed:")
        for x in over[:3]:
            add(f"  ⚠ overdue · P{x.get('priority', 3)} · {x.get('title', '?')[:50]}")
        for x in due[:3]:
            add(f"  ⏰ 24h · P{x.get('priority', 3)} · {x.get('title', '?')[:50]}")
    else:
        add("📋 *Tasks*: ✓ none urgent")
    add("")

    # ---- Calendar ----
    c = (b.get("personal") or {}).get("calendar") or {}
    if c.get("status") == "no-scope":
        add("📅 *Calendar*: no scope")
    elif c.get("status") == "degraded":
        add(f"📅 *Calendar*: ⚠ {c.get('error', 'degraded')}")
    else:
        events = c.get("events") or []
        today = _dt.datetime.now().date()
        soon = []
        for e in events:
            t_iso = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date")
            if not t_iso:
                continue
            try:
                d = _dt.datetime.fromisoformat(t_iso.replace("Z", "+00:00"))
            except Exception:
                continue
            if (d.date() - today).days <= 1:
                soon.append((d, e))
        if soon:
            add("📅 *Calendar* (next 24h):")
            for d, e in soon[:5]:
                add(f"  · {_format_event_time(e.get('start', {}))} · {e.get('summary', '?')[:50]}")
        else:
            add("📅 *Calendar*: nothing in next 24h ✓")
    add("")

    # ---- Inbox ----
    g = (b.get("personal") or {}).get("gmail") or {}
    if g.get("status") == "no-scope":
        add("📨 *Inbox*: no scope")
    elif g.get("status") == "degraded":
        add(f"📨 *Inbox*: ⚠ {g.get('error', 'degraded')}")
    else:
        msgs = [m for m in (g.get("messages") or []) if "UNREAD" in (m.get("labels") or [])]
        if msgs:
            add(f"📨 *Inbox*: {len(msgs)} unread — top 3:")
            for m in msgs[:3]:
                add(f"  · {m.get('from', '?')[:30]} · {m.get('subject', '?')[:50]}")
        else:
            add("📨 *Inbox*: ✓ no unread")
    add("")

    # ---- Research ----
    rp = b.get("research") or {}
    projs = rp.get("projects") or []
    if rp.get("error"):
        add(f"🔬 *Research*: ⚠ {rp.get('error')}")
    elif projs:
        add(f"🔬 *Research* ({len(projs)} active):")
        for p in projs[:3]:
            conf = p.get("confidence_overall")
            conf_s = f"{conf:.2f}" if conf is not None else "—"
            qo = p.get("questions_open")
            qo_n = len(qo) if isinstance(qo, list) else (qo or 0)
            add(f"  · {p.get('title', '?')[:50]}")
            add(f"    conf {conf_s} · Q{qo_n} open · E{p.get('evidence_total', 0)} · {_rel_time(p.get('last_active'))}")
    else:
        add("🔬 *Research*: no active projects ✓")
    add("")

    # ---- Watchdog ----
    w = b.get("watchdog") or {}
    if w.get("_fallback"):
        probes = w.get("probes") or {}
        if probes:
            failed = [n for n, v in probes.items() if not v.get("ok")]
            if failed:
                add(f"🛡 *Watchdog*: ⚠ {len(failed)} failing: {', '.join(failed[:3])}")
            else:
                add("🛡 *Watchdog*: ✓ all green")
    else:
        svcs = w.get("services") or {}
        failed = [n for n, v in svcs.items() if not v.get("ok")]
        if failed:
            add(f"🛡 *Watchdog*: ⚠ {len(failed)} failing: {', '.join(failed[:3])}")
        elif svcs:
            add(f"🛡 *Watchdog*: ✓ all {len(svcs)} services green")
        else:
            add("🛡 *Watchdog*: no data")
    add("")

    # ---- Recent syntheses ----
    rs = b.get("recent_syntheses") or {}
    if rs.get("_status") == 500 or rs.get("error"):
        add("🔍 *Recent syntheses*: (endpoint gated — see /briefing dashboard)")
    else:
        items = (rs.get("syntheses") or [])
        if items:
            add(f"🔍 *Recent syntheses* ({len(items)}):")
            for s in items[:2]:
                add(f"  · {s.get('project_slug', '?')} · {s.get('question', '?')[:55]}")
        else:
            add("🔍 *Recent syntheses*: none yet")

    msg = "\n".join(lines).strip()
    if len(msg) > 4000:
        msg = msg[:3950] + "\n_…(truncated, see /briefing dashboard)_"
    return msg


def _post_telegram(message: str) -> bool:
    """Post the briefing to the connected home channel via send_message.

    Tries the send_message tool first (delivers to the active channel).
    If that fails or isn't available, logs the error but doesn't raise —
    the briefing is also saved to disk for the dashboard to show.
    """
    try:
        from hermes_tools import send_message  # type: ignore
        send_message(action="send", message=message)
        return True
    except ImportError:
        _log("send_message tool unavailable — message not delivered")
    except Exception as e:
        _log(f"telegram post failed: {type(e).__name__}: {e}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes daily briefing aggregator")
    ap.add_argument("--post", action="store_true", help="Post to Telegram home channel")
    ap.add_argument("--json", action="store_true", help="Write JSON snapshot only (no Telegram)")
    args = ap.parse_args()

    b = _build_briefing()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(b, indent=2, default=str), encoding="utf-8")
    _log(f"snapshot saved: {SNAPSHOT_PATH.name} ({len(json.dumps(b))} bytes)")

    msg = _format_telegram(b)
    if args.post:
        ok = _post_telegram(msg)
        _log(f"telegram post: {'ok' if ok else 'FAILED'}")
        # Cron delivery: short single-line confirmation. The actual
        # briefing text already went to Telegram via _post_telegram.
        if ok:
            print(f"✓ morning briefing delivered · {len(msg)} chars · {b['generated_at']}")
        else:
            # Failure path: print the full message so the cron channel
            # at least gets *something* and the user can see it failed.
            print("⚠ telegram post failed; briefing text follows:")
            print(msg)
            return 1
    elif args.json:
        pass
    else:
        print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
