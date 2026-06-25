# Data sources for COMEX / warehouse inventory

Last verified: 2026-06-17. Status can change; re-run `python ...\fetch_comex_inventory.py --metal silver --validate` to confirm.

## Summary

| Source | URL pattern | What it gives | Status | Why |
|---|---|---|---|---|
| **CME Group** | `https://www.cmegroup.com/delivery_reports/Silver_stocks.xls` | Authoritative registered + eligible per warehouse | ❌ **BLOCKED** | IP-blocked from this host; commercial API only |
| **SilverData.io** | `https://silverdata.io/inventories` (silver), `/gold-inventories` (gold) | Total, registered, eligible, registered-ratio for the whole COMEX complex | ✅ **PRIMARY** | Plain text, parses cleanly with regex, free, no auth |
| **MetalCharts.org** | `https://metalcharts.org/comex/silver` | Total inventory (RSC-streamed, registered/eligible split is hydrated JS) | ⚠️ **FALLBACK** | Total works via static HTML; split needs Playwright hydration |
| **GoldSilver.ai** | `https://goldsilver.ai/metal-prices/comex-silver` | Registered, eligible, total, delivery pressure | ⚠️ **FALLBACK** | Next.js SPA, needs Playwright; has the registered/eligible split |
| **MacroMicro** | `https://en.macromicro.me/series/17517/silver-comex-warehouse-stock` | Total COMEX warehouse stock (no split) | ❌ **PARTIAL** | Cloudflare-walled, only the total is in the SSR HTML |
| **TrendForce DataTrack** | `https://datatrack.trendforce.com/Chart/content/1545/comex-inventory-silver` | Historical registered + eligible | ❌ **GATED** | Auth-token API; not scriptable without a license |
| **CFTC COT** | `https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm` | Weekly disaggregated commercials' shorts/longs vs OI | ⚠️ **SECONDARY** | Friday 3:30pm ET only; great for sentiment, not daily monitoring |
| **TradingView MCP** | `mcp_tradingview_yahoo_price` | Spot price, OHLC | ❌ **WRONG SIGNAL** | Price != inventory; useful alongside, not as primary |

## Why the obvious choice (CME) doesn't work

`https://www.cmegroup.com/delivery_reports/Silver_stocks.xls` returns a 602-byte JSON response:

```json
{
  "message": "This IP address is blocked due to suspected web scraping activity associated with it on this CMEgroup.com page. Use of scripts, software, spiders, robots, avatars, agents, tools or other scraping mechanisms is strictly prohibited by CME Group's website Data Terms of Use. If you are attempting to access data or content from the website via automated means or for commercial purposes, CME has numerous other methods to deliver the content you require."
}
```

Same 403 from `CmeWS/mvc/Quotes/*` and `CmeWS/mvc/Settlements/*`. CME's commercial market-data APIs exist but require a paid license and an OAuth API key. Don't waste time retrying with different User-Agents — they fingerprint the IP.

## Why SilverData.io works

The site is a static-rendered Astro/Next.js-style page with the inventory cards emitted as plain text in the post-hydration `document.body.innerText`. The exact strings we match are:

- `COMEX Registered\nDELIVERABLE\n86M oz\nAvailable for futures delivery`
- `COMEX Eligible\n235M oz\nMeets exchange standards`
- `COMEX Total\n321M oz\n+1.9% (30d)`
- `Registered Ratio\n26.9%\n% of COMEX that's deliverable`

If SilverData.io redesigns these cards, the regexes in `fetch_comex_inventory.py` will stop matching and the script will exit 2 with a "missing pattern" error. The `--validate` mode is built to catch this loudly. Re-validate quarterly.

## Fallback strategy (if SilverData.io goes away)

1. **Switch to GoldSilver.ai** — same data, different DOM. Needs a new set of regexes. The page URL is `https://goldsilver.ai/metal-prices/comex-silver`. The numbers render after JS hydration, so Playwright is required.
2. **Last resort: MetalCharts.org + commercial COT** — MetalCharts has the total inventory reliably. Combine with the weekly CFTC COT for the commercials' net short position as a proxy for deliverable-supply pressure. Less precise but still directional.

## Adding a new source

If you find a better source (e.g., a paid API, a different aggregator), add an entry to `METAL_CONFIGS` in `scripts/fetch_comex_inventory.py` and a row to the table above. The script is designed so a source is just `{url, patterns: {...}}` — no other plumbing needed.

## Multi-metal caveats

SilverData.io also has a gold page (`/gold-inventories`) with the same DOM structure, so gold is "free" once silver works. The script already has a `gold` config entry, but the patterns are speculative — they were never actually validated against the live page. **Before relying on the gold signal, run:**

```bash
python fetch_comex_inventory.py --metal gold --validate
```

If the parse fails, open the gold-inventories page in a browser, dump the post-hydration innerText, and update the patterns in the `gold` config block.

For copper, platinum, palladium — SilverData.io's coverage is unclear. The CFTC publishes a COT report for copper that captures the commercials' net short, which is the cleanest available proxy for physical tightness in the base metals.
