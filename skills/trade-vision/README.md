# Trade Vision — Short-Dated Covered-Call Advisor

Daily 4:30pm ET post-market analysis for short-dated (0-7 DTE) covered calls on
AGQ/MSTR/TSLA. Maximizes premium income, minimizes share-assignment risk.

## TL;DR

| Component | What it does |
|-----------|--------------|
| `daily_analysis.py` | Cron entry: refreshes OHLCV, computes Markov, pulls options, scores CCs, writes digest |
| `markov_matrix.py` | First-order Markov chain on daily returns — direction bias for next 1-3 days |
| `options_screen.py` | Multi-backend options chain screener (Tradier preferred, yfinance fallback) |
| `cc_analyzer.py` | Black-Scholes + Monte Carlo + Markov + earnings-penalty strike scoring |
| `trade_journal.py` | SQLite CRUD for closed CCs + standalone HTTP API for the dashboard |
| `mcp_options_helper.py` | Bridge script between TV MCP options tools and the Python logic |
| Dashboard | `/trade-vision` tab in Hermes — recs, journal, market regime |
| Pine scripts | `references/pine_scripts/` — 5 indicators for TradingView Desktop |

## What runs when

```
trade-vision-daily cron @ 4:30pm ET Mon-Fri (cron expr: 30 16 * * 1-5)
        │
        ▼
daily_analysis.py
        │
        ├── Sync time-series (yfinance → SQLite)
        ├── Markov direction per ticker (markov_matrix.py)
        ├── Options chain (options_screen.py → yfinance or Tradier)
        ├── Score CC candidates (cc_analyzer.py)
        ├── Write daily_digest_<YYYY-MM-DD>.md
        └── Mirror to open-notebook as note
```

## Setup status

| Component | Status | Notes |
|-----------|--------|-------|
| Open-notebook notebooks (6) | ✅ Created | Master + Market + TSLA + MSTR + AGQ + Crypto + Trades |
| `research_project_mcp` master | ✅ Created | `trade-vision-portfolio` with 5 hypotheses + 5 questions |
| Time-series (9 series) | ✅ Backfilled | TSLA/MSTR/AGQ/SOL/BTC/HBAR/SPY/VIX/QQQ with 1 year of OHLCV |
| Trade-journal SQLite + HTTP server | ✅ Running | port 9118, 3 sample trades |
| Pine scripts (5) | ✅ Written | Markov, IV rank, S/R, Earnings, CC cones |
| TV MCP options tools | ✅ Built | `options_chain`, `options_expirations`, `options_screen`, `options_greeks` |
| Cron job (daily 4:30pm ET) | ✅ Registered | id `0137440662e0`, deliver to telegram |
| Dashboard plugin | ✅ Built | `/trade-vision` tab in Hermes |
| First end-to-end run | ✅ Verified | `daily_analysis.py` produces digest |

## Quick commands

```bash
# Run pipeline manually (any time)
python C:/Data/Hermes/skills/trade-vision/scripts/daily_analysis.py

# Markov direction only
python C:/Data/Hermes/skills/trade-vision/scripts/markov_matrix.py

# Options chain for a ticker
python C:/Data/Hermes/skills/trade-vision/scripts/options_screen.py TSLA --max-dte 7

# CC scoring for a ticker
python C:/Data/Hermes/skills/trade-vision/scripts/cc_analyzer.py MSTR --max-dte 7

# Trade journal
python C:/Data/Hermes/skills/trade-vision/scripts/trade_journal.py init
python C:/Data/Hermes/skills/trade-vision/scripts/trade_journal.py log-open \
    --ticker TSLA --strike 450 --expiration 2026-06-27 --contracts 1 \
    --premium 4.50 --intent cc_50pct_decay
python C:/Data/Hermes/skills/trade-vision/scripts/trade_journal.py list --status open
python C:/Data/Hermes/skills/trade-vision/scripts/trade_journal.py stats
python C:/Data/Hermes/skills/trade-vision/scripts/trade_journal.py serve --port 9118
```

## Adding a Tradier API key (recommended for production)

yfinance options data has quality issues for short-dated strikes (low IVs, stale quotes). For real CC recommendations:

1. Sign up at https://developer.tradier.com/ (free sandbox)
2. Add `TRADIER_API_KEY` to `C:/Data/Hermes/.env`
3. Restart the tradingview MCP (or wait for the next daily pipeline run)
4. The system auto-detects `TRADIER_API_KEY` and switches backend

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  daily_analysis │───▶│  options_screen  │───▶│ Tradier / Yahoo  │
│     .py         │    │       .py        │    │   options API    │
└────────┬────────┘    └──────────────────┘    └─────────────────┘
         │
         ▼
┌─────────────────┐    ┌──────────────────┐
│   cc_analyzer   │───▶│  Markov matrix   │───▶ time-series DB
│      .py        │    │       .py        │     (yfinance cache)
└────────┬────────┘    └──────────────────┘
         │
         ▼
┌─────────────────┐
│  daily_digest   │───▶ open-notebook note
│   _<DATE>.md    │───▶ Telegram (via cron)
└─────────────────┘
```

## Trade journal

3 sample trades are pre-seeded for testing:
- TSLA CC $435 @ $4.10 → closed @ $2.05 (50% rule) → +$205
- MSTR CC $145 @ $1.85 → closed @ $0.95 → +$90
- AGQ CC $113 @ $1.82 → closed @ $1.50 → +$32

Total: $327 P&L, 100% win rate, 33% exit discipline (TSLA hit 50% rule).

## Dashboard

The dashboard plugin registers a `/trade-vision` tab in the Hermes dashboard
(`http://127.0.0.1:9119/`). It shows:
- Market regime (SPY/QQQ/VIX + crypto sentiment)
- Portfolio holdings
- Today's CC recommendations
- Trade journal (open positions + stats)
- Log Trade form
- Latest daily digest (markdown)

## Risk rules (hardcoded, not overridable)

1. **Never recommend strike <2% OTM** (delta > 0.50)
2. **AGQ CCs always carry vol-decay warning** (~3%/wk at high RV)
3. **Skip CCs in the 7 days before earnings** (assignment risk spike)
4. **Max position per ticker**: 25-50% of holding (per portfolio_constraints.md)
5. **Crypto is sentiment-only**: SOL/HBAR/BTC never appear in recommendations

## See also

- `references/cc_methodology.md` — Black-Scholes + Monte Carlo + exit strategy
- `references/markov_model.md` — Quant direction model spec
- `references/portfolio_constraints.md` — Position limits and risk rules
- `references/pine_scripts/README.md` — TradingView indicator docs
- `templates/daily_report.md` — Daily digest template
- `data/trade-journal.db` — Closed trades
- `data/daily_digest_<DATE>.md` — Latest digest output