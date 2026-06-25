"""COMEX / SilverData adapter.

source_args schema::

    {
        "metal": "silver",          # required — silver | gold | copper | platinum | palladium
        "metric": "registered",     # optional — registered | eligible | total | ratio
                                     #  'ratio' returns registered / (registered + eligible)
    }

This adapter is intentionally thin — it delegates to the
``commodity-inventory-monitor`` skill's existing fetcher (Playwright via
silverdata.io). For now the fetch is synchronous; the dashboard already
collects this data daily, so the MCP can backfill by reading the skill's
output CSV from ``C:\\Data\\Hermes\\data\\commodity-inventory\\``.

A future revision can call the skill's fetcher directly via subprocess.
For Phase 2, the recommended flow is:

  1. The commodity-inventory-monitor cron writes its daily snapshot to
     ``C:\\Data\\Hermes\\data\\commodity-inventory\\<metal>_<YYYY-MM-DD>.json``
  2. The time-series MCP's series_sync() reads the latest snapshot and
     appends the metric point.

This module implements step 2 — given a path glob, find the latest snapshot,
parse it, and return a single metric record.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


SNAPSHOT_DIR = Path(r"C:\Data\Hermes\data\commodity-inventory")


def fetch(source_args: dict, from_ts: str, to_ts: str) -> list[dict]:
    metal = source_args.get("metal")
    if not metal:
        raise ValueError("comex adapter requires source_args.metal")
    metric = source_args.get("metric") or "registered"

    # Find latest snapshot file for this metal within the requested window
    candidates: list[Path] = []
    if SNAPSHOT_DIR.exists():
        for p in SNAPSHOT_DIR.glob(f"{metal}_*.json"):
            candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    out: list[dict] = []
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = data.get("as_of") or data.get("timestamp") or p.stem.split("_")[-1]
        # Normalize ts to ISO 8601
        try:
            ts_iso = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            ts_iso = ts
        if from_ts and ts_iso < from_ts:
            continue
        if to_ts and ts_iso > to_ts:
            continue

        value = _extract_metric(data, metric)
        if value is None:
            continue
        out.append({
            "kind": "metric",
            "ts": ts_iso,
            "value": float(value),
            "meta": {
                "metal": metal,
                "metric": metric,
                "source_file": p.name,
            },
        })

    return out


def _extract_metric(data: dict, metric: str) -> Optional[float]:
    """Pull a metric out of a commodity snapshot payload."""
    if metric == "registered":
        v = data.get("registered") or data.get("registered_oz")
        return _to_float(v)
    if metric == "eligible":
        v = data.get("eligible") or data.get("eligible_oz")
        return _to_float(v)
    if metric == "total":
        v = data.get("total") or data.get("total_oz")
        return _to_float(v)
    if metric == "ratio":
        r = _to_float(data.get("registered") or data.get("registered_oz"))
        e = _to_float(data.get("eligible") or data.get("eligible_oz"))
        if r is None or e is None:
            return None
        denom = r + e
        return (r / denom) if denom > 0 else None
    return None


def _to_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
