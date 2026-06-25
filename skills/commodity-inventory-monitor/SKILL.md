---
name: commodity-inventory-monitor
description: "Monitor COMEX / warehouse registered-vs-eligible metal inventory and alert on squeeze precursors. Use when the user wants a daily/weekly inventory check for silver, gold, copper, or other metals, asks to watch a 'registered/total' ratio, wants a market-pulse section in a digest, or needs to set up threshold-based alerts on physical metal availability. Works around the fact that the CME website and most metal-charting aggregators are walled off from scripted access — this skill uses headless Chromium (Playwright) against the public SilverData.io site, which exposes the registered/eligible split in plain text that is parseable via regex."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [commodities, silver, gold, copper, comex, inventory, alerts, cron, investment, portfolio, playwright, web-scraping]
    related_skills: [daily-news-digest, webwright]
---

# Commodity Inventory Monitor

A reusable pattern for watching physical metal inventory in COMEX (and similar exchange) warehouses, computing the **registered / (registered + eligible)** ratio, and alerting when that ratio drops below a configurable threshold. The signal: when registered (deliverable) silver gets too thin relative to the total vault, paper shorts cannot be settled in metal — that is the squeeze precursor.

This skill exists because **the obvious data sources don't work for scripting**: CME's own site IP-blocks automated access, most charting sites (MetalCharts, GoldSilver.ai, MacroMicro) are Next.js SPAs that need a headless browser, and TrendForce gates its data behind a tokenized API. The one public site that exposes the split in plain text is `silverdata.io/inventories` — see `references/data_sources.md` for the full source-rotation table.

## When to use this skill

- "Set up a daily inventory check for [metal]"
- "Watch the registered/eligible ratio and alert me if it drops"
- "Add a market pulse / inventory section to my daily news digest"
- "Is silver/gold/copper getting tight in the vaults?"
- Any threshold-based alert on physical metal availability

If the user just wants today's spot price or a chart, this is overkill — use the TradingView MCP or `mcp_tradingview_yahoo_price` directly. This skill is for *sustained, threshold-based monitoring* of structural tightness, not spot queries.

## Stack & dependencies

- **Playwright 1.60+** with Chromium headless binary. The Windows venv at `C:\Data\Hermes\hermes-agent\.venv\Scripts\python.exe` already has it; check with `python -c "from playwright.sync_api import sync_playwright; print('ok')"`. If missing: `pip install playwright && playwright install chromium`.
- **Python 3.11+** (whatever the active venv ships).
- No `pandas`, no `openpyxl` — the script is stdlib + Playwright only.
- Reads the page's `document.body.innerText` and matches with regex. Brittle to site redesigns, hence the `--validate` mode and the source-rotation fallback.

## The recipe

### 1. Pick a metal and threshold

See `references/thresholds.md` for recommended starting points. The default for silver is **0.25** (Bill's setting — a 25% registered share is the squeeze trigger). Gold and copper have different baselines because their market structure is different.

### 2. Run the script once to confirm data flow

```bash
python C:\Data\Hermes\skills\commodity-inventory-monitor\scripts\fetch_comex_inventory.py \
    --metal silver \
    --threshold 0.25 \
    --state-file C:\Data\Hermes\cache\comex_inventory_history.jsonl
```

Expected output (one JSON line on stdout):

```json
{
  "ts": "2026-06-17T01:10:00+00:00",
  "metal": "silver",
  "source": "silverdata.io",
  "fetch_ok": true,
  "registered_oz": 86000000,
  "eligible_oz": 235000000,
  "total_oz": 321000000,
  "registered_ratio": 0.2679,
  "site_reported_ratio": 0.269,
  "threshold": 0.25,
  "alert": false
}
```

### 3. Wire it into a cron job (or run ad-hoc)

The script is designed to be called from any agent prompt. Drop the text in `templates/cron_prompt_addendum.md` into your job's prompt as a "preflight" step. The full integration with the `daily-news-digest` cron is the reference implementation — see `templates/cron_prompt_addendum.md` and the existing job `1a810ce29751` ("Daily News Review") for the pattern.

### 4. Read the trend

```bash
python fetch_comex_inventory.py --metal silver --trend --days 30
```

Prints a markdown table of the last N daily readings. Use this to spot slow draws before they trigger the alert — a ratio that drops from 0.32 → 0.28 over 30 days is a different signal than a sudden 0.27 → 0.20 flash crash.

### 5. Validate after site redesigns

```bash
python fetch_comex_inventory.py --metal silver --validate
```

Loads the page, runs the parsers, and exits non-zero with a descriptive error if any field is missing or the ratios are wildly out of range. Run this manually after you see "data unavailable" for 2+ consecutive days, or quarterly as a sanity check. The script saves a small "last-validated" marker so you can see how long the source has been trusted.

## Output contract

The script always emits **one JSON object on stdout** with these fields:

| Field | Type | Meaning |
|---|---|---|
| `ts` | ISO8601 UTC | When the fetch happened |
| `metal` | str | `silver` / `gold` / `copper` / etc. |
| `source` | str | Which site served the data |
| `fetch_ok` | bool | Did we get a clean parse? |
| `registered_oz` | int | Deliverable stock (absolute oz) |
| `eligible_oz` | int | Non-deliverable but spec-meeting stock |
| `total_oz` | int | Sum of the two |
| `registered_ratio` | float (0-1) | registered / total, computed by us |
| `site_reported_ratio` | float (0-1) | What the site itself shows (sanity check) |
| `threshold` | float | What the user asked us to alert at |
| `alert` | bool / null | `true` if `registered_ratio < threshold`, `null` if `fetch_ok=false` |

Exit codes: `0` = clean fetch and parse, `2` = fetched but parse failed (data structure changed), `1` = network/playwright error. Cron jobs should treat any non-zero as "show the user a degraded message" — never block the parent job on this.

## State file format

`--state-file PATH` (default: `C:\Data\Hermes\cache\comex_inventory_history.jsonl`) appends one JSON object per line per fetch. This is a JSON-Lines file (not JSON array) so partial writes don't corrupt the history. The `--trend` mode reads this file.

Recommended retention: keep the history forever (it's tiny, ~150 bytes per row). The trend report is most useful across months, not weeks.

## Pitfalls

### Don't trust CME.com from a script

`https://www.cmegroup.com/delivery_reports/Silver_stocks.xls` returns a JSON-formatted 403 block with the message *"This IP address is blocked due to suspected web scraping activity"* — confirmed on this host. The CME Data APIs are paid commercial products. Don't burn time trying to bypass; use SilverData.io instead.

### Most aggregators are JS SPAs

MetalCharts.org, GoldSilver.ai, and MacroMicro all render the registered/eligible split via React Server Components that don't include the numbers in the initial HTML. Plain `curl` gets a near-empty page. Playwright is mandatory; the regex parsing happens against the post-hydration `innerText`.

### "M oz" rounding causes a ~0.1% parse error

SilverData.io displays "86M oz" for registered, which truncates the underlying integer. The site's own "Registered Ratio" uses the precise numbers (26.9%); our regex-derived ratio (26.79%) has a 0.11-point gap. Both round to 0.27 — neither crosses the 0.25 threshold today — but the gap grows as registered approaches zero. If the ratio is at 0.25x and the site says 0.26x, treat the *lower* number as the real value when sizing positions.

### Playwright is slow — 30 to 60 seconds per fetch

The Chromium cold start dominates. If the cron fires 5–10 minutes before it needs to deliver, that's fine. If you need a faster path, switch the source to a paid API or build a small cache layer. **Don't** try to keep a persistent browser alive between cron runs — that introduces lifetime / zombie-process issues that aren't worth the 20-second saving.

### SilverData.io is a single point of failure

If they redesign the page, take it offline, or rate-limit, the alert stops working. Mitigations:
1. The script has a `--validate` mode that detects parse failures loudly (exit 2, not silent).
2. `references/data_sources.md` lists two fallback sources (MetalCharts, GoldSilver.ai) that also need Playwright but parse differently — if you have time, add a third source/extractor in `fetch_comex_inventory.py`.
3. Cron job prompt says "if fetch_ok is false, ship the news digest without the Market Pulse section" — degrade gracefully rather than fail.

### CFTC COT is weekly, not daily

The Commitments of Traders report is published every Friday at 3:30pm ET for the prior Tuesday. It's a great *secondary* signal (commercial shorts vs OI shows who's positioned for delivery) but it won't give you daily data. Don't try to use it as the primary feed.

## Verification checklist (run before trusting a fresh install)

- [ ] `python -c "from playwright.sync_api import sync_playwright; print('ok')"` — confirms Playwright is in the active venv
- [ ] `python ...\fetch_comex_inventory.py --metal silver --validate` — exits 0, prints a "validated" line with current numbers
- [ ] `python ...\fetch_comex_inventory.py --metal silver --threshold 0.99` — `alert: true` (forces the alert path)
- [ ] `python ...\fetch_comex_inventory.py --metal silver --threshold 0.01` — `alert: false` (forces the normal path)
- [ ] `python ...\fetch_comex_inventory.py --metal silver --trend --days 7` — prints a markdown table of the last week's readings
- [ ] Open the history file: `cat C:\Data\Hermes\cache\comex_inventory_history.jsonl | tail -5` — should be one JSON object per line, growing by 1 per fetch

## Related

- **`daily-news-digest`** — the cron-job pattern this skill was originally wired into. See the existing job `1a810ce29751` for the integration.
- **`webwright`** — for tasks that need a *real* browser session (login, interactive scraping). This skill does NOT need it — Playwright headless is enough for read-only public pages.
- **`TradingView MCP`** (`mcp_tradingview_yahoo_price` etc.) — for spot price queries, not inventory.
- **`SKILL.md authoring` skill** — if you fork this skill for another metal, follow that skill's frontmatter spec.
