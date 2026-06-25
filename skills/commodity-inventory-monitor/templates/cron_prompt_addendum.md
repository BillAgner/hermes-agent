# Cron-prompt addendum

Drop the following block into your cron job's prompt as a preflight step. The script is self-contained, returns a single JSON line on stdout, and degrades gracefully if the data source is down.

## Block 1 — Preflight (insert at the top of the prompt)

```markdown
0. **COMEX silver preflight** (run first, every time — this runs before the news check so a hard alert can lead the digest):
   - Execute exactly: `python C:\Data\Hermes\skills\commodity-inventory-monitor\scripts\fetch_comex_inventory.py --metal silver --threshold 0.25 --state-file C:\Data\Hermes\cache\comex_inventory_history.jsonl`
   - Parse the JSON line on stdout. Capture these fields for later: `registered_oz`, `eligible_oz`, `total_oz`, `registered_ratio`, `site_reported_ratio`, `alert`, `fetch_ok`.
   - If `fetch_ok` is false (data source unreachable / parse error), treat silver as "data unavailable" and continue — the news digest MUST still ship. Never block the digest on the silver check.
   - If `alert` is true, the registered-vs-total ratio has dropped below Bill's 0.25 (25%) trigger. The squeeze precursor is active.
```

## Block 2 — Compose (insert before the "Deliver" step)

```markdown
5. **Compose the final message**:
   - Run the digest skill to produce the news digest body.
   - **ALERT prefix (conditional)**: If `alert` is true, prepend the following block ABOVE the news digest:
     ```
     🚨 COMEX SILVER ALERT — registered ratio at {registered_ratio*100:.1f}% (below your 0.25 trigger).
     Registered: {registered_oz/1e6:.0f}M oz | Eligible: {eligible_oz/1e6:.0f}M oz | Total: {total_oz/1e6:.0f}M oz
     Registered stock thin enough to signal squeeze dynamics. See today's digest for context.
     ```
   - **Market Pulse (always, when fetch_ok)**: Append the following block to the END of the message:
     ```
     ---
     📊 Market Pulse — COMEX Silver
     Registered: {registered_oz/1e6:.0f}M oz ({registered_ratio*100:.1f}% of total)
     Eligible:   {eligible_oz/1e6:.0f}M oz
     Total:      {total_oz/1e6:.0f}M oz
     Source: silverdata.io
     Status: {ALERT — below 0.25 threshold | normal | unavailable}
     ```
   - If `fetch_ok` is false, omit the Market Pulse block entirely (don't ship partial data).
```

## Adapting for other metals

Replace `silver` with `gold` or `copper` in the script call. Update the threshold to match `references/thresholds.md` (e.g., `--threshold 0.50` for gold). Update the displayed emoji and label in Block 2's Market Pulse section accordingly.

## Adapting for a different cron job

If your cron job doesn't have a "compose final message" step (e.g., it's a watchdog that just emits one fact), the preflight block alone is enough — the JSON output is your deliverable. Example for a pure alert job:

```markdown
You are the COMEX silver watchdog. Every 30 minutes, run:
  python C:\Data\Hermes\skills\commodity-inventory-monitor\scripts\fetch_comex_inventory.py --metal silver --threshold 0.25

If the JSON has `alert: true`, deliver that line verbatim to telegram.
If `alert: false` or `fetch_ok: false`, emit nothing (silent watchdog).
```

## Example: full replacement of the silver section in the existing daily-news-review prompt

The job `1a810ce29751` (Daily News Review) has been wired with this pattern. The full new prompt body is reproduced here as a working reference:

```markdown
0. **COMEX silver preflight** (run first, every time — this runs before the news check so a hard alert can lead the digest):
   - Execute exactly: `python C:\Data\Hermes\skills\commodity-inventory-monitor\scripts\fetch_comex_inventory.py --metal silver --threshold 0.25 --state-file C:\Data\Hermes\cache\comex_inventory_history.jsonl`
   - Parse the JSON line on stdout. Capture these fields for later: `registered_oz`, `eligible_oz`, `total_oz`, `registered_ratio`, `site_reported_ratio`, `alert`, `fetch_ok`.
   - If `fetch_ok` is false (data source unreachable / parse error), treat silver as "data unavailable" and continue — the news digest MUST still ship. Never block the digest on the silver check.
   - If `alert` is true, the registered-vs-total ratio has dropped below Bill's 0.25 (25%) trigger. The squeeze precursor is active.

1. **Find today's news video**: ... (your existing step)

...

5. **Compose the final message**:
   - Run the daily-news-digest skill to produce the news digest body.
   - **ALERT prefix (conditional)**: If `alert` is true, prepend:
     ```
     🚨 COMEX SILVER ALERT — registered ratio at {registered_ratio*100:.1f}% (below your 0.25 trigger).
     Registered: {registered_oz/1e6:.0f}M oz | Eligible: {eligible_oz/1e6:.0f}M oz | Total: {total_oz/1e6:.0f}M oz
     Registered stock thin enough to signal squeeze dynamics. See today's digest for context.
     ```
   - **Market Pulse (always, when fetch_ok)**: Append at the END:
     ```
     ---
     📊 Market Pulse — COMEX Silver
     Registered: {registered_oz/1e6:.0f}M oz ({registered_ratio*100:.1f}% of total)
     Eligible:   {eligible_oz/1e6:.0f}M oz
     Total:      {total_oz/1e6:.0f}M oz
     Source: silverdata.io
     Status: {ALERT — below 0.25 threshold | normal}
     ```
   - If `fetch_ok` is false, omit the Market Pulse block entirely.

6. **Deliver the digest** to the configured target. (your existing step)
```
