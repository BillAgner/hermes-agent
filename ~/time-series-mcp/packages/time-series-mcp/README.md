# time-series-mcp

Local-cached, backtest-ready financial time-series data via MCP.

Stores metric points and OHLCV bars in SQLite (`C:\Data\Hermes\finance\data\timeseries.db`).
Fetches from yahoo (yfinance), comex (commodity-inventory-monitor snapshots), and
generic HTTP/JSON endpoints. Links series to research_project_mcp projects.

## Tools

- `series_health` — DB path, row counts
- `series_create` — register a new series (name, metric, unit, cadence, source_type, source_args)
- `series_get` / `series_list` — metadata
- `series_append` / `series_append_batch` — write points
- `series_query` — read points in a [from, to] range
- `series_backfill` — fetch + cache from external source over a window
- `series_sync` — refresh latest-known point from external source
- `series_status` — freshness, gap detection
- `series_link` / `series_links_for_project` — research project integration

## Source types

- `yahoo` — yfinance; needs `source_args.symbol` (e.g. `"AAPL"`)
- `comex` — reads `C:\Data\Hermes\data\commodity-inventory\<metal>_*.json`; needs `source_args.metal`
- `generic_http` — arbitrary JSON endpoint; needs `url`, `jsonpath_records`, `jsonpath_ts`, `jsonpath_value`
- `manual` — `series_append` only, no adapter fetch
