"""COMEX / warehouse inventory fetcher for precious and base metals.

Fetches daily registered + eligible inventory from a public source (currently
SilverData.io), computes the registered/(registered+eligible) ratio, and
emits a single JSON line on stdout. Designed to be called from a cron job
agent or invoked ad-hoc.

Why Playwright? The official CME data is IP-blocked from this host, and the
public aggregators (MetalCharts, GoldSilver.ai) are Next.js SPAs that don't
expose the split in the initial HTML. SilverData.io is the one public site
that renders the numbers in plain text, but it still needs a real browser
to execute the ApexCharts hydration before the values are queryable.

Why regex on innerText? It's brittle to site redesigns, but it's the
lightest-weight way to get the data without a paid API. The --validate
mode catches parse drift loudly (exit 2) instead of silently shipping
bad numbers.

Usage:
    python fetch_comex_inventory.py --metal silver --threshold 0.25
    python fetch_comex_inventory.py --metal silver --trend --days 30
    python fetch_comex_inventory.py --metal silver --validate

Exit codes:
    0 = clean fetch and parse
    1 = network / Playwright error
    2 = fetched but parse failed (data structure changed)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_NAV_TIMEOUT_MS = 45_000
DEFAULT_HISTORY_PATH = r"C:\Data\Hermes\cache\comex_inventory_history.jsonl"

# Per-metal config: which page to hit, and the regex patterns to extract the
# three core numbers from the page's innerText. Order of patterns matters —
# longest/most-specific first, and a stricter pattern for each metal.
#
# To add a new metal:
#   1. Pick a source page that exposes registered + eligible + total.
#   2. Open the page in Playwright and dump innerText.
#   3. Add a dict entry to METAL_CONFIGS below with the URL and three
#      regex patterns that match "Registered / N M oz", etc.
#   4. Add the metal to SILVERDATA_PATTERN_OVERRIDES if the registered/label
#      words differ (e.g. "Eligible" vs "Non-registered").
#
# Sanity bounds: registered_oz and total_oz should be positive. The ratio
# is checked against [0, 1]. Anything outside that range is treated as a
# parse error (exit 2).

METAL_CONFIGS: dict[str, dict[str, Any]] = {
    "silver": {
        "url": "https://silverdata.io/inventories",
        # SilverData.io renders cards with structure:
        #   "COMEX Registered\nDELIVERABLE\n86M oz\nAvailable for futures delivery"
        #   "COMEX Eligible\n235M oz\nMeets exchange standards"
        #   "COMEX Total\n321M oz\n+1.9% (30d)"
        # and a side panel with:
        #   "Registered Ratio\n26.9%\n% of COMEX that's deliverable"
        "patterns": {
            "registered_oz_million": re.compile(
                r"COMEX Registered\s+DELIVERABLE\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "eligible_oz_million": re.compile(
                r"COMEX Eligible\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "total_oz_million": re.compile(
                r"COMEX Total\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "site_ratio_pct": re.compile(
                r"Registered Ratio\s+([\d\.]+)\s*%",
                re.IGNORECASE,
            ),
        },
    },
    # GOLD: SilverData.io has gold too — https://silverdata.io/gold-inventories
    # Same DOM structure. The patterns below are speculative (untested) and
    # should be re-validated before relying on. Kept here as a starting point.
    "gold": {
        "url": "https://silverdata.io/gold-inventories",
        "patterns": {
            "registered_oz_million": re.compile(
                r"COMEX Registered\s+DELIVERABLE\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "eligible_oz_million": re.compile(
                r"COMEX Eligible\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "total_oz_million": re.compile(
                r"COMEX Total\s+([\d\.]+)\s*M\s*oz",
                re.IGNORECASE,
            ),
            "site_ratio_pct": re.compile(
                r"Registered Ratio\s+([\d\.]+)\s*%",
                re.IGNORECASE,
            ),
        },
    },
}


def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _fetch_page_text(url: str, nav_timeout_ms: int) -> str:
    """Open the inventory page in headless Chromium and return innerText."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.set_default_navigation_timeout(nav_timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            # ApexCharts takes a moment to render — wait for a known card.
            try:
                page.wait_for_selector("text=COMEX Registered", timeout=15_000)
            except Exception:
                page.wait_for_load_state("networkidle", timeout=10_000)
            time.sleep(1.5)
            return page.evaluate("() => document.body.innerText")
        finally:
            browser.close()


def _parse(metal: str, text: str) -> dict[str, Any]:
    cfg = METAL_CONFIGS[metal]
    out: dict[str, Any] = {"fetch_ok": False, "errors": []}

    for key, pat in cfg["patterns"].items():
        m = pat.search(text)
        if not m:
            out["errors"].append(f"missing pattern: {key}")
            continue
        try:
            out[key] = float(m.group(1))
        except ValueError:
            out["errors"].append(f"non-numeric match for {key}: {m.group(1)}")

    if out["errors"]:
        return out

    out["registered_oz"] = int(out.pop("registered_oz_million") * 1_000_000)
    out["eligible_oz"] = int(out.pop("eligible_oz_million") * 1_000_000)
    out["total_oz"] = int(out.pop("total_oz_million") * 1_000_000)
    site_ratio = out.pop("site_ratio_pct") / 100.0
    computed_ratio = out["registered_oz"] / out["total_oz"] if out["total_oz"] else 0.0

    # Sanity bounds
    if not (0.0 <= computed_ratio <= 1.0):
        out["errors"].append(f"ratio out of range: {computed_ratio}")
        return out
    if out["total_oz"] < 1_000_000:
        out["errors"].append(f"total too small: {out['total_oz']}")
        return out

    out["registered_ratio"] = round(computed_ratio, 4)
    out["site_reported_ratio"] = round(site_ratio, 4)
    out["fetch_ok"] = True
    return out


def _append_state(state_file: Path | None, record: dict[str, Any]) -> None:
    """Append a record to a JSON-Lines history file (best-effort)."""
    if not state_file:
        return
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with state_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception as exc:  # pragma: no cover
        print(f"WARN: failed to append state to {state_file}: {exc}", file=sys.stderr)


def _read_state(state_file: Path) -> list[dict[str, Any]]:
    """Read all records from a JSON-Lines history file."""
    if not state_file.exists():
        return []
    out = []
    for line in state_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _print_trend(metal: str, days: int, state_file: Path) -> int:
    records = [r for r in _read_state(state_file) if r.get("metal") == metal and r.get("fetch_ok")]
    if not records:
        print(f"No history records for {metal} in {state_file}", file=sys.stderr)
        return 1

    # Filter to last N days
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    recent = []
    for r in records:
        try:
            ts = _dt.datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        if ts >= cutoff:
            recent.append(r)

    if not recent:
        print(f"No {metal} records in the last {days} days", file=sys.stderr)
        return 1

    print(f"# {metal.upper()} COMEX inventory — last {len(recent)} readings (last {days} days)")
    print()
    print("| Date (UTC) | Registered (M oz) | Eligible (M oz) | Total (M oz) | Ratio | Alert |")
    print("|---|---|---|---|---|---|")
    for r in recent:
        d = r["ts"][:10]
        reg = r.get("registered_oz", 0) / 1_000_000
        elig = r.get("eligible_oz", 0) / 1_000_000
        tot = r.get("total_oz", 0) / 1_000_000
        ratio = r.get("registered_ratio", 0) * 100
        alert = "🚨" if r.get("alert") else ""
        print(f"| {d} | {reg:.1f} | {elig:.1f} | {tot:.1f} | {ratio:.2f}% | {alert} |")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch COMEX registered/eligible inventory for a metal, compute ratio, optionally alert.",
    )
    ap.add_argument("--metal", required=True, choices=sorted(METAL_CONFIGS.keys()),
                    help="Which metal to query")
    ap.add_argument("--threshold", type=float, default=None,
                    help="Alert if registered_ratio falls below this value (e.g. 0.25 for silver). "
                         "If omitted, alert is computed but the field is null.")
    ap.add_argument("--state-file", type=Path, default=Path(DEFAULT_HISTORY_PATH),
                    help=f"Where to append daily readings (default {DEFAULT_HISTORY_PATH})")
    ap.add_argument("--trend", action="store_true",
                    help="Print a markdown trend table from history instead of fetching")
    ap.add_argument("--days", type=int, default=30,
                    help="When used with --trend, only show the last N days (default 30)")
    ap.add_argument("--validate", action="store_true",
                    help="Fetch + parse + sanity-check, exit loudly on any failure")
    ap.add_argument("--no-state", action="store_true",
                    help="Don't write to the state file (for ad-hoc / test runs)")
    ap.add_argument("--nav-timeout", type=int, default=DEFAULT_NAV_TIMEOUT_MS // 1000,
                    help="Page navigation timeout in seconds (default 45)")
    args = ap.parse_args()

    if args.trend:
        return _print_trend(args.metal, args.days, args.state_file)

    cfg = METAL_CONFIGS[args.metal]
    record: dict[str, Any] = {
        "ts": _now_utc_iso(),
        "metal": args.metal,
        "source": cfg["url"],
        "threshold": args.threshold,
    }

    try:
        text = _fetch_page_text(cfg["url"], args.nav_timeout * 1000)
    except Exception as exc:
        record["fetch_ok"] = False
        record["error"] = f"fetch failed: {exc!r}"
        print(json.dumps(record, sort_keys=True))
        return 1

    parsed = _parse(args.metal, text)
    record.update(parsed)

    if record.get("fetch_ok"):
        if args.threshold is not None:
            record["alert"] = bool(record["registered_ratio"] < args.threshold)
        else:
            record["alert"] = None
    else:
        record["alert"] = None

    if not args.no_state:
        _append_state(args.state_file, record)

    print(json.dumps(record, sort_keys=True))

    if args.validate:
        if not record.get("fetch_ok"):
            print(f"VALIDATION FAILED: {record.get('errors') or record.get('error')}", file=sys.stderr)
            return 2
        print(f"VALIDATION OK: {args.metal} ratio {record['registered_ratio']*100:.2f}% "
              f"({record['registered_oz']/1e6:.0f}M / {record['total_oz']/1e6:.0f}M oz)")

    return 0 if record.get("fetch_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
