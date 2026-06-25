---
name: trade-vision
description: "Short-term (0-7 DTE) covered-call advisor for AGQ/MSTR/TSLA. Pulls OHLCV via time_series, options chains via Tradier-backed TradingView MCP tools, runs Black-Scholes + Monte Carlo + Markov direction scoring, generates strike/exit recommendations, and posts a daily digest. Includes Pine scripts for IV-rank, Markov direction, and probability cones. Use when the user asks for a CC recommendation on a holding, wants the daily Trade Vision digest, or wants to log/report a closed trade."
platforms: [windows]
metadata:
  hermes:
    tags: [trading, options, covered-calls, portfolio, markov, black-scholes, monte-carlo, AGQ, MSTR, TSLA, BTC, SOL, HBAR, daily-digest, cron]
    related_skills: [time-series, open-notebook, research-project, tradingview-desktop, source-credibility]
---

# Trade Vision — Short-Dated Covered-Call Advisor

## What this skill does

1. **Daily 4:30pm ET cron** runs the full analysis pipeline:
   - Refreshes OHLCV for all holdings + market indices (time-series)
   - Computes Markov transition matrix for each ticker
   - Pulls live options chains via `tv_options_chain` (Tradier-backed)
   - Scores each 0-7 DTE call strike by:
     - Black-Scholes theoretical price vs bid
     - Monte Carlo probability of finishing ITM
     - Distance-from-spot buffer (in ATR units)
     - Earnings / catalyst proximity penalty
     - Markov directional bias adjustment
   - Ranks top 3 strikes per ticker
   - Saves recommendations to the trade-vision-portfolio open-notebook
   - Posts the digest to Telegram

2. **Per-stock analysis** (TSLA/MSTR/AGQ): fundamentals, technicals, catalysts, sentiment — saved to per-stock notebooks via on_create_note.

3. **Market regime context**: SPY/QQQ/VIX daily snapshot — saved to market notebook.

4. **Trade journal**: SQLite-backed CRUD at `data/trade-journal.db`. Dashboard form for logging closed trades. Win-rate + exit-discipline stats.

5. **Pine scripts** (compiled in TV Desktop): Markov direction, IV rank/percentile, support/resistance, earnings proximity, CC probability cones.

## How to invoke

| Bill says | What happens |
|-----------|--------------|
| "Trade Vision" or "run trade vision" | Manual trigger of daily pipeline (post-market) |
| "log trade" or "closed MSTR CC..." | Open dashboard log-trade form OR run `scripts/trade_journal.py log` |
| "TSLA analysis" or "MSTR snapshot" | Pull latest per-stock report from open-notebook |
| "VIX today" or "market pulse" | Pull latest market snapshot |

## Components

| Path | Purpose |
|------|---------|
| `scripts/daily_analysis.py` | Cron entry point: full daily pipeline |
| `scripts/cc_analyzer.py` | Covered-call scoring engine (BS + MC + Markov + catalyst) |
| `scripts/markov_matrix.py` | First-order Markov chain on daily returns |
| `scripts/options_screen.py` | Tradier options chain screening |
| `scripts/trade_journal.py` | SQLite CRUD for closed trades |
| `scripts/fundamentals.py` | Per-stock fundamentals + catalyst proximity |
| `references/cc_methodology.md` | Black-Scholes + Monte Carlo + exit-strategy spec |
| `references/markov_model.md` | Quant direction model spec |
| `references/portfolio_constraints.md` | Position limits, risk rules |
| `templates/daily_report.md` | Daily digest template |
| `data/trade-journal.db` | SQLite: closed trades |

## Pipeline output

Each run writes:
1. **`C:/Data/Hermes/skills/trade-vision/data/daily_digest_<YYYY-MM-DD>.md`** — full human-readable digest
2. **open-notebook note** in `Trade Vision Portfolio` notebook — AI-style summary
3. **time-series append** — today's prices + computed metrics (IV-rank, Markov probability)
4. **Telegram post** (if configured) — top 3 strikes per ticker

## Risk rules (hardcoded, not overridable)

- **Never recommend a strike <2% OTM** (delta > 0.50). Buffer > 2% is mandatory.
- **AGQ CCs are flagged**: every recommendation includes a `vol_decay_warning: true` line because AGQ's 2x daily leverage erodes share value independent of strike outcome.
- **Skip recommendations** if next earnings < 7 days out (assignment risk spike).
- **Max position per ticker**: 100% of holding × (single-strike delta cap of 0.30). Don't over-allocate.
- **Crypto is sentiment-only**: SOL/HBAR/BTC never appear in the recommendations list.

## Notebooks

| Notebook ID | Purpose |
|-------------|---------|
| `notebook:2zz45n8ip3uu68chl8qt` | `[rp] trade-vision-portfolio` — master research_project with hypotheses |
| `notebook:0tm6vpes6gtdly3n3qeh` | `Trade Vision Market` — daily broad-market digest |
| `notebook:8xoppcplzw2uvr2rfyr5` | `Trade Vision TSLA` — per-stock daily |
| `notebook:2thlme432rth2oq3b9xz` | `Trade Vision MSTR` — per-stock daily |
| `notebook:l1mu8epypy8k1vjilbif` | `Trade Vision AGQ` — per-stock daily (with vol-decay warnings) |
| `notebook:hzthpj4681zv68x88jir` | `Trade Vision Crypto` — SOL/HBAR/BTC sentiment |
| `notebook:guyjgypj5m6q9xrxmlbk` | `Trade Vision Trades` — closed-trade log mirror |

## Files

- Source: `C:\Data\Hermes\skills\trade-vision\`
- Trade journal DB: `C:\Data\Hermes\skills\trade-vision\data\trade-journal.db`
- TV MCP options extensions: `C:\Data\Hermes\~\tradingview-mcp\src\tools\options.js` + `core/options.js`
- Pine scripts: in `references/pine_scripts/` (compiled into TV Desktop)
- Cron job: `trade-vision-daily` at `30 16 * * 1-5` (4:30pm ET, weekdays)

## Setup status (initial build)

- [x] 6 open-notebook notebooks created
- [x] 1 master research_project with 5 hypotheses + 5 open questions
- [x] 9 time-series registered (TSLA/MSTR/AGQ/SOL/BTC/HBAR/SPY/VIX/QQQ)
- [x] 264+ bars backfilled per series
- [ ] Options chain MCP tools (Tradier-backed) — building
- [ ] CC scoring engine — building
- [ ] Markov direction model — building
- [ ] Pine scripts — building
- [ ] Daily analysis pipeline (cron script) — building
- [ ] Trade journal SQLite — building
- [ ] Dashboard page — building
- [ ] Cron job registered — building
- [ ] First end-to-end run verified — pending

See `references/` for methodology details and `templates/daily_report.md` for the digest format.