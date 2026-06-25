"""Yahoo Finance adapter via yfinance.

source_args schema::

    {
        "symbol": "AAPL",                    # required — Yahoo ticker
        "interval": "1d",                    # optional, default "1d"
        "metric": "close"                    # optional, default "close" (also: open/high/low/adjclose)
    }

Returns a list of OHLCV-shaped records (always). For a single-metric series,
the MCP caller can configure metric=close and treat the close column as the
canonical value; the OHLCV fields are still populated.
"""

from __future__ import annotations

from typing import Any


def fetch(source_args: dict, from_ts: str, to_ts: str) -> list[dict]:
    symbol = source_args.get("symbol")
    if not symbol:
        raise ValueError("yahoo adapter requires source_args.symbol")

    interval = source_args.get("interval") or "1d"

    # Import yfinance lazily so the MCP can start even if yfinance isn't installed
    # in the active venv (degrades to manual entries + generic_http).
    import yfinance as yf  # type: ignore[import-not-found]

    ticker = yf.Ticker(symbol)
    # yfinance accepts YYYY-MM-DD strings for start/end; auto_adjust=True returns
    # adjusted prices (splits + dividends baked in), which is what most backtests want.
    df = ticker.history(
        start=from_ts[:10],
        end=_bump_day(to_ts[:10]),  # yfinance end is exclusive
        interval=interval,
        auto_adjust=True,
        actions=False,
    )
    if df is None or df.empty:
        return []

    out: list[dict] = []
    for ts_idx, row in df.iterrows():
        # ts_idx is a pandas.Timestamp — convert to ISO 8601 UTC
        try:
            ts_iso = ts_idx.tz_convert("UTC").isoformat() if ts_idx.tzinfo else ts_idx.isoformat() + "Z"
        except Exception:
            ts_iso = str(ts_idx)
        out.append({
            "kind": "ohlcv",
            "ts": ts_iso,
            "open":  _safe_float(row.get("Open")),
            "high":  _safe_float(row.get("High")),
            "low":   _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("Volume")),
        })
    return out


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        # yfinance uses NaN for missing; keep None so JSON serialisation is clean
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _bump_day(yyyy_mm_dd: str) -> str:
    """Add one day to an ISO date string. yfinance's `end` parameter is exclusive."""
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(yyyy_mm_dd)
        return (d + timedelta(days=1)).isoformat()
    except ValueError:
        return yyyy_mm_dd
