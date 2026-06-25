"""Source adapters for fetching time-series data.

Each adapter exposes a single ``fetch()`` function that takes the source_args
dict (as stored in series_meta.source_args) plus a window (from/to dates) and
returns a list of normalized records.

Two record shapes are produced:
  - ``{"kind": "metric", "ts": ISO, "value": float, "meta": dict}``
  - ``{"kind": "ohlcv",  "ts": ISO, "open":.., "high":.., "low":.., "close":.., "volume":..}``

The MCP server dispatches to the right adapter by ``source_type``.
"""

from time_series_mcp.adapters import comex, generic_http, yahoo

ADAPTERS = {
    "yahoo": yahoo,
    "comex": comex,
    "generic_http": generic_http,
}


def fetch(source_type: str, source_args: dict, from_ts: str, to_ts: str) -> list[dict]:
    """Dispatch to the right adapter. Raises ValueError on unknown type."""
    if source_type not in ADAPTERS:
        raise ValueError(
            f"unknown source_type {source_type!r}; expected one of {list(ADAPTERS)}"
        )
    return ADAPTERS[source_type].fetch(source_args, from_ts, to_ts)
