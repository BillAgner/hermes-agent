"""time-series-mcp FastMCP server.

Eleven tools, all namespaced ``series_*`` to keep them distinct from
generic verbs in other MCPs::

    series_health         — DB path, row counts, series count
    series_create         — register a new series
    series_get            — fetch metadata for one series (by id or name)
    series_list           — list all registered series
    series_append         — append a single metric point or OHLCV bar
    series_append_batch   — bulk append metric or OHLCV points
    series_query          — read points in a [from, to] range
    series_backfill       — fetch + cache from external source over a window
    series_sync           — refresh latest-known point from external source
    series_status         — freshness, row counts, gap detection
    series_link           — connect a series to a research project

NOTE: Do NOT add ``from __future__ import annotations`` to this file —
it makes annotations into strings and breaks FastMCP's tool-decorator
typing. Annotations below use bare types (``X = None``, not ``Optional[X]``).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from time_series_mcp.__about__ import __version__
from time_series_mcp import storage
from time_series_mcp.adapters import fetch as adapter_fetch


mcp = FastMCP("time-series")


# ---- helpers --------------------------------------------------------------

def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_series_id(series_id_or_name) -> int:
    """Accept either int id or string name; return int id."""
    if isinstance(series_id_or_name, int):
        return series_id_or_name
    s = str(series_id_or_name).strip()
    if s.isdigit():
        return int(s)
    meta = storage.get_series(storage.DEFAULT_DB_PATH, s)
    return int(meta["id"])


def _error(msg: str) -> str:
    return _to_json({"success": False, "error": msg})


# ---- tools ----------------------------------------------------------------

@mcp.tool()
def series_health() -> str:
    """Server status: version, DB path, row counts."""
    return _to_json({
        "success": True,
        "version": __version__,
        "storage": storage.db_summary(storage.DEFAULT_DB_PATH),
    })


@mcp.tool()
def series_create(
    name: str,
    metric: str,
    unit: str,
    cadence: str,
    source_type: str,
    source_args: dict = None,
    description: str = "",
) -> str:
    """Register a new series.

    Args:
        name: Unique slug-like name (e.g. 'AAPL-close-1d').
        metric: Human metric (e.g. 'close', 'registered_oz').
        unit: Unit string (e.g. 'USD', 'oz').
        cadence: Sampling cadence. One of: tick, 1m, 5m, 15m, 1h, 4h, 1d, 1w, 1M.
        source_type: Adapter. One of: yahoo, comex, generic_http, manual.
        source_args: Adapter-specific args dict (e.g. {'symbol': 'AAPL'}).
        description: Free-form description.
    """
    try:
        meta = storage.create_series(
            db_path=storage.DEFAULT_DB_PATH,
            name=name,
            metric=metric,
            unit=unit,
            cadence=cadence,
            source_type=source_type,
            source_args=source_args or {},
            description=description,
            now_iso=_now_iso(),
        )
        return _to_json({"success": True, "series": meta})
    except storage.SeriesAlreadyExistsError as e:
        return _error(f"series already exists: {e}")
    except ValueError as e:
        return _error(str(e))


@mcp.tool()
def series_get(series_id_or_name) -> str:
    """Fetch metadata for one series by id (int) or name (str)."""
    try:
        meta = storage.get_series(storage.DEFAULT_DB_PATH, series_id_or_name)
        return _to_json({"success": True, "series": meta})
    except storage.SeriesNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def series_list() -> str:
    """List all registered series."""
    return _to_json({"success": True, "series": storage.list_series(storage.DEFAULT_DB_PATH)})


@mcp.tool()
def series_append(
    series_id: int,
    ts: str,
    value: float = None,
    meta: dict = None,
    open: float = None,
    high: float = None,
    low: float = None,
    close: float = None,
    volume: float = None,
) -> str:
    """Append a single point. If open/high/low/close are provided, writes an OHLCV bar;
    otherwise writes a metric point using (value, meta).

    Args:
        series_id: Numeric series id (use series_list to find).
        ts: ISO 8601 timestamp.
        value: Numeric value (metric series).
        meta: Optional metadata dict.
        open/high/low/close/volume: OHLCV fields (bar series).
    """
    is_ohlcv = open is not None and high is not None and low is not None and close is not None
    try:
        if is_ohlcv:
            storage.append_ohlcv(
                storage.DEFAULT_DB_PATH, series_id, ts,
                open=open, high=high, low=low, close=close, volume=volume,
            )
            kind = "ohlcv"
        else:
            if value is None:
                return _error("either `value` or all of (open, high, low, close) must be provided")
            storage.append_metric(storage.DEFAULT_DB_PATH, series_id, ts, value, meta or {})
            kind = "metric"
        return _to_json({"success": True, "series_id": series_id, "ts": ts, "kind": kind})
    except Exception as e:
        return _error(str(e))


@mcp.tool()
def series_append_batch(series_id: int, points: list) -> str:
    """Bulk-append points. ``points`` is a list of dicts; each dict is either::

        {"ts": "...", "value": 123.4, "meta": {...}}         # metric

    or::

        {"ts": "...", "open":.., "high":.., "low":.., "close":.., "volume":..}    # OHLCV

    Idempotent on (series_id, ts) — re-sending the same point updates it.
    """
    written = 0
    skipped = 0
    for p in points:
        try:
            ts = p["ts"]
            if all(k in p for k in ("open", "high", "low", "close")):
                storage.append_ohlcv(
                    storage.DEFAULT_DB_PATH, series_id, ts,
                    open=float(p["open"]), high=float(p["high"]),
                    low=float(p["low"]), close=float(p["close"]),
                    volume=(float(p["volume"]) if p.get("volume") is not None else None),
                )
            else:
                storage.append_metric(
                    storage.DEFAULT_DB_PATH, series_id, ts,
                    float(p["value"]), p.get("meta") or {},
                )
            written += 1
        except Exception:
            skipped += 1
    return _to_json({"success": True, "series_id": series_id, "written": written, "skipped": skipped})


@mcp.tool()
def series_query(
    series_id: int,
    from_ts: str = None,
    to_ts: str = None,
    limit: int = 10000,
) -> str:
    """Read points in [from_ts, to_ts], ascending by ts.

    Auto-detects OHLCV vs metric table based on the series' source_type.
    For OHLCV series, returns bars; for others, returns metric points.
    """
    try:
        meta = storage.get_series(storage.DEFAULT_DB_PATH, series_id)
        if meta["source_type"] in ("yahoo",):
            rows = storage.query_ohlcv(storage.DEFAULT_DB_PATH, series_id, from_ts, to_ts, limit)
            kind = "ohlcv"
        else:
            rows = storage.query_metric(storage.DEFAULT_DB_PATH, series_id, from_ts, to_ts, limit)
            kind = "metric"
        return _to_json({
            "success": True,
            "series_id": series_id,
            "series_name": meta["name"],
            "kind": kind,
            "row_count": len(rows),
            "rows": rows,
        })
    except storage.SeriesNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def series_backfill(
    series_id: int,
    from_ts: str,
    to_ts: str,
) -> str:
    """Fetch from external source over [from_ts, to_ts] and cache every point.

    For yahoo: pulls OHLCV via yfinance. For comex: reads snapshot files in
    C:\\Data\\Hermes\\data\\commodity-inventory\\. For generic_http: hits the URL.
    """
    try:
        meta = storage.get_series(storage.DEFAULT_DB_PATH, series_id)
        records = adapter_fetch(meta["source_type"], meta["source_args"], from_ts, to_ts)
    except storage.SeriesNotFoundError as e:
        return _error(str(e))
    except Exception as e:
        return _error(f"fetch failed: {e}")

    written = 0
    for rec in records:
        try:
            if rec["kind"] == "ohlcv":
                storage.append_ohlcv(
                    storage.DEFAULT_DB_PATH, series_id, rec["ts"],
                    open=rec["open"], high=rec["high"],
                    low=rec["low"], close=rec["close"],
                    volume=rec.get("volume"),
                )
            else:
                storage.append_metric(
                    storage.DEFAULT_DB_PATH, series_id, rec["ts"],
                    rec["value"], rec.get("meta") or {},
                )
            written += 1
        except Exception:
            pass
    storage.mark_synced(storage.DEFAULT_DB_PATH, series_id, _now_iso())
    return _to_json({
        "success": True,
        "series_id": series_id,
        "fetched": len(records),
        "written": written,
        "from_ts": from_ts,
        "to_ts": to_ts,
    })


@mcp.tool()
def series_sync(series_id: int, lookback_days: int = 7) -> str:
    """Refresh from external source, looking back ``lookback_days`` from today."""
    from datetime import timedelta
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=lookback_days)
    return series_backfill(series_id, from_dt.isoformat(), to_dt.isoformat())


@mcp.tool()
def series_status(series_id: int) -> str:
    """Freshness, row count, gap detection for a series."""
    try:
        return _to_json({"success": True, **storage.series_status(storage.DEFAULT_DB_PATH, series_id)})
    except storage.SeriesNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def series_link(series_id: int, project_slug: str, ref_type: str, ref_id: str) -> str:
    """Link a series to a research project (idempotent).

    Args:
        series_id: Numeric series id.
        project_slug: Slug of the research_project_mcp project.
        ref_type: One of: hypothesis, evidence, question, project.
        ref_id: The id within the project (e.g. 'H1', 'E3', 'Q2').
    """
    try:
        storage.link_series(
            storage.DEFAULT_DB_PATH, series_id, project_slug, ref_type, ref_id, _now_iso(),
        )
        return _to_json({
            "success": True,
            "series_id": series_id,
            "project_slug": project_slug,
            "ref_type": ref_type,
            "ref_id": ref_id,
        })
    except Exception as e:
        return _error(str(e))


@mcp.tool()
def series_links_for_project(project_slug: str) -> str:
    """List all series linked to a research project."""
    return _to_json({
        "success": True,
        "project_slug": project_slug,
        "links": storage.list_links_for_project(storage.DEFAULT_DB_PATH, project_slug),
    })


def main() -> None:
    # Initialise DB on startup so health checks pass even before first write.
    storage.init_db(storage.DEFAULT_DB_PATH)
    mcp.run()


if __name__ == "__main__":
    main()
