"""
Daily Analysis Pipeline — runs once per day post-market (4:30pm ET).
Refreshes time-series, computes Markov direction, pulls options chains,
scores CC candidates, writes the digest, and mirrors to open-notebook.

This is the cron entry point. Designed to be called by the trade-vision-daily
cron job. Idempotent — re-running for the same day overwrites the digest.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path

# Add the script directory to sys.path so we can import sibling modules
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

import markov_matrix
import options_screen
import cc_analyzer


# Per-ticker config
PORTFOLIO = {
    "TSLA": {
        "shares": 6800,
        "series_id": 3,
        "is_leveraged_etf": False,
        "earnings_hint_days": None,  # TODO: pull from fundamentals table
        "notebook_id": "notebook:8xoppcplzw2uvr2rfyr5",
    },
    "MSTR": {
        "shares": 1430,
        "series_id": 4,
        "is_leveraged_etf": False,
        "earnings_hint_days": None,
        "notebook_id": "notebook:2thlme432rth2oq3b9xz",
    },
    "AGQ": {
        "shares": 300,
        "series_id": 5,
        "is_leveraged_etf": True,
        "earnings_hint_days": None,
        "notebook_id": "notebook:l1mu8epypy8k1vjilbif",
    },
}

MARKET_TICKERS = {
    "SPY": 9,
    "QQQ": 11,
    "VIX": 10,
}
CRYPTO_TICKERS = {
    "BTC": 7,
    "SOL": 6,
    "HBAR": 8,
}

DATA_DIR = Path(r"C:/Data/Hermes/skills/trade-vision/data")
TIME_SERIES_DB = r"C:/Data/Hermes/finance/data/timeseries.db"


def sync_time_series(ticker: str, series_id: int) -> int:
    """Refresh latest OHLCV for a series. Returns rows added.

    Uses yfinance to pull last 7 days of bars and appends any new ones to the
    local SQLite cache. The MCP's series_sync is exposed via JSON-RPC; we use
    the storage module directly for in-process calls.
    """
    try:
        import yfinance as yf
        from datetime import timedelta
        from time_series_mcp import storage

        # Get symbol from series_meta
        meta = storage.get_series(storage.DEFAULT_DB_PATH, series_id)
        if not meta:
            return 0
        source_args = meta.get("source_args", {})
        symbol = source_args.get("symbol")
        if not symbol:
            return 0

        # Pull last 7 days from yfinance
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        t = yf.Ticker(symbol)
        hist = t.history(start=start.date().isoformat(), end=end.date().isoformat(), interval="1d")
        if hist.empty:
            return 0

        rows_added = 0
        for ts, row in hist.iterrows():
            ts_iso = ts.tz_localize("UTC").isoformat() if ts.tz is None else ts.tz_convert("UTC").isoformat()
            try:
                storage.append_ohlcv(
                    storage.DEFAULT_DB_PATH,
                    series_id,
                    ts_iso,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row["Volume"]) if "Volume" in row else None,
                )
                rows_added += 1
            except Exception:
                # Duplicate (already cached) — that's fine
                pass

        storage.mark_synced(storage.DEFAULT_DB_PATH, series_id, datetime.now(timezone.utc).isoformat())
        return rows_added
    except Exception as e:
        print(f"  [WARN] time-series sync failed for {ticker}: {e}")
        return 0


def format_digest(ticker: str, recs: list, spot: float, markov: dict, decay_wk: float = 0) -> str:
    """Format a per-ticker digest section."""
    if not recs:
        return f"### {ticker} (${spot:.2f})\n\nNo qualifying CC candidates today.\n\n"

    lines = [f"### {ticker} (${spot:.2f})",
             "",
             f"- Markov direction: P(up 3d)={markov.get('p_up_3d', 0):.3f}",
             f"- Current state: {markov.get('current_state', '?')}",
             f"- Confidence: {markov.get('confidence', 0):.2f}",
             ""]

    if decay_wk > 0:
        lines.append(f"> **AGQ vol-decay warning**: ~{decay_wk:.2%}/week from compounding rebalancing")
        lines.append("")

    lines.append("**Top CC candidates:**")
    lines.append("")
    lines.append("| Strike | Exp | DTE | Δ | Buffer | P(ITM) | AdjP | Premium | Score |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in recs:
        from dataclasses import asdict
        if hasattr(r, "__dataclass_fields__"):
            d = asdict(r)
        else:
            d = r
        flags = ",".join(d.get("risk_flags", [])) if d.get("risk_flags") else ""
        flag_str = f" _{flags}_" if flags else ""
        lines.append(
            f"| ${d['strike']:.2f} | {d['expiration']} | {d['dte']} | {d['delta']:.3f} "
            f"| {d['buffer_atr']:.2f}x | {d['p_itm']*100:.1f}% | {d['p_itm_adjusted']*100:.1f}% "
            f"| {d['premium_pct']*100:.2f}% | {d['score']:.1f}{flag_str} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_digest(today_str: str, sections: dict, meta: dict) -> Path:
    """Write the markdown digest to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / f"daily_digest_{today_str}.md"

    header = [
        f"# Trade Vision Daily Digest — {today_str}",
        "",
        f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
        f"*Cron: trade-vision-daily | 30 16 \* \* 1-5 (4:30pm ET)*",
        "",
        "---",
        "",
        "## TL;DR",
        "",
    ]

    # Count recommendations
    n_recs = sum(len(s.get('recs', [])) for s in sections.values())
    tickers_with_recs = [t for t, s in sections.items() if s.get('recs')]
    header.append(f"- **Total CC candidates across {len(PORTFOLIO)} tickers**: {n_recs}")
    if tickers_with_recs:
        header.append(f"- **Tickers with viable trades**: {', '.join(tickers_with_recs)}")
    else:
        header.append("- **No viable trades today** — review market conditions")
    header.append(f"- **Market regime**: VIX = {meta.get('vix_close', 0):.2f}, SPY = ${meta.get('spy_close', 0):.2f}")
    header.append("")

    # Per-ticker sections
    body = []
    for ticker in ["TSLA", "MSTR", "AGQ"]:
        s = sections.get(ticker, {})
        body.append(format_digest(
            ticker=ticker,
            recs=s.get("recs", []),
            spot=s.get("spot", 0),
            markov=s.get("markov", {}),
            decay_wk=s.get("agq_decay_wk", 0),
        ))

    # Market regime section
    body.append("---")
    body.append("")
    body.append("## Market Regime")
    body.append("")
    body.append(f"- **SPY**: ${meta.get('spy_close', 0):.2f}")
    body.append(f"- **QQQ**: ${meta.get('qqq_close', 0):.2f}")
    body.append(f"- **VIX**: {meta.get('vix_close', 0):.2f}")
    body.append("")
    body.append("**Crypto sentiment (no recommendations):**")
    body.append(f"- **BTC**: ${meta.get('btc_close', 0):.2f}")
    body.append(f"- **SOL**: ${meta.get('sol_close', 0):.2f}")
    body.append(f"- **HBAR**: ${meta.get('hbar_close', 0):.2f}")
    body.append("")

    # Risk section
    body.append("---")
    body.append("")
    body.append("## What could go wrong")
    body.append("")
    body.append("- **Volatility crush**: realized vol drops below implied before expiry, slowing premium decay")
    body.append("- **Earnings surprise**: TSLA/MSTR/AGQ can move 5-10% on announcements")
    body.append("- **Macro shock**: CPI/FOMC/jobs data can invalidate technical setups")
    body.append("- **AGQ-specific**: even if CC expires OTM, share value erodes due to vol decay")
    body.append("- **Trade journal**: check dashboard /trade-vision for closed-trade stats")
    body.append("")

    out_file.write_text("\n".join(header + body + ["\n"]), encoding="utf-8")
    return out_file


def write_to_notebook(notebook_id: str, title: str, content: str) -> bool:
    """Mirror the digest to open-notebook via REST API.

    Creates a note via POST /api/notes. Note: open-notebook's REST API doesn't
    have a "attach note to notebook" endpoint we can hit cleanly; the notebook
    UI mirrors project evidence automatically via the rp_mcp_ mirror, but raw
    AI notes created here appear in the global notes list.
    """
    import urllib.request
    base = "http://localhost:5055/api"
    payload = {
        "title": title,
        "content": content,
        "note_type": "ai",
    }
    try:
        req = urllib.request.Request(
            f"{base}/notes",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return result.get("id") is not None
    except Exception as e:
        print(f"  [WARN] open-notebook write failed: {e}")
        return False


def fetch_latest_close(series_id: int) -> float:
    """Get the latest close for a time-series."""
    conn = sqlite3.connect(TIME_SERIES_DB)
    c = conn.cursor()
    c.execute(
        "SELECT close FROM ohlcv_bars WHERE series_id=? ORDER BY ts DESC LIMIT 1",
        (series_id,),
    )
    row = c.fetchone()
    conn.close()
    return float(row[0]) if row else 0.0


def main():
    """Run the full daily pipeline."""
    today_str = date.today().isoformat()
    print(f"=== Trade Vision daily pipeline — {today_str} ===")
    print()

    sections = {}

    # 1. Sync time-series for all portfolio tickers
    print("1. Syncing time-series...")
    for ticker, cfg in PORTFOLIO.items():
        rows = sync_time_series(ticker, cfg["series_id"])
        print(f"   {ticker}: {rows} rows synced")

    # 2. Compute Markov for each portfolio ticker
    print()
    print("2. Computing Markov direction model...")
    markov_states = {}
    for ticker, cfg in PORTFOLIO.items():
        state = markov_matrix.compute_markov(
            ticker=ticker,
            db_path=TIME_SERIES_DB,
            series_id=cfg["series_id"],
        )
        if state:
            from dataclasses import asdict
            markov_states[ticker] = asdict(state)
            print(f"   {ticker}: state={state.current_state} "
                  f"P(up 1d)={state.p_up_1d:.3f} 3d={state.p_up_3d:.3f} "
                  f"conf={state.confidence:.2f}")

    # 3. Pull options chains and score CC candidates
    print()
    print("3. Scoring CC candidates...")
    for ticker, cfg in PORTFOLIO.items():
        print(f"   {ticker}:")
        try:
            markov_p_up = markov_states.get(ticker, {}).get("p_up_3d", 0.5)

            # AGQ decay estimation
            agq_decay = 0.0
            if cfg["is_leveraged_etf"]:
                closes, _ = cc_analyzer.get_recent_ohlc(TIME_SERIES_DB, cfg["series_id"], days=60)
                agq_decay = cc_analyzer.estimate_agq_decay(closes)

            calls, spot, meta = options_screen.screen(
                ticker=ticker, min_dte=0, max_dte=7, prefer="auto",
            )

            recs = cc_analyzer.score_chain(
                ticker=ticker,
                calls=calls,
                spot=spot,
                markov_p_up_3d=markov_p_up,
                earnings_days=cfg.get("earnings_hint_days"),
                is_leveraged_etf=cfg["is_leveraged_etf"],
                agq_decay_per_week=agq_decay,
                db_path=TIME_SERIES_DB,
                series_id=cfg["series_id"],
            )

            sections[ticker] = {
                "spot": spot,
                "recs": recs,
                "markov": markov_states.get(ticker, {}),
                "agq_decay_wk": agq_decay,
                "meta": meta,
            }
            print(f"     spot=${spot:.2f}, {len(recs)} viable trades, "
                  f"decay={agq_decay:.2%}" if agq_decay else f"     spot=${spot:.2f}, {len(recs)} viable trades")
            for r in recs[:3]:
                print(f"     - strike=${r.strike} exp={r.expiration} "
                      f"delta={r.delta:.3f} prem={r.premium_pct*100:.2f}% score={r.score:.1f}")
        except Exception as e:
            print(f"     [ERROR] {e}")
            sections[ticker] = {"spot": 0, "recs": [], "markov": {}, "agq_decay_wk": 0}

    # 4. Fetch market regime data
    print()
    print("4. Fetching market regime data...")
    meta_market = {
        "spy_close": fetch_latest_close(MARKET_TICKERS["SPY"]),
        "qqq_close": fetch_latest_close(MARKET_TICKERS["QQQ"]),
        "vix_close": fetch_latest_close(MARKET_TICKERS["VIX"]),
        "btc_close": fetch_latest_close(CRYPTO_TICKERS["BTC"]),
        "sol_close": fetch_latest_close(CRYPTO_TICKERS["SOL"]),
        "hbar_close": fetch_latest_close(CRYPTO_TICKERS["HBAR"]),
    }
    for k, v in meta_market.items():
        print(f"   {k}: {v:.2f}" if v else f"   {k}: (no data)")

    # 5. Write digest
    print()
    print("5. Writing digest...")
    digest_path = write_digest(today_str, sections, meta_market)
    print(f"   Wrote {digest_path}")

    # 6. Mirror to open-notebook
    print()
    print("6. Mirroring to open-notebook...")
    master_notebook = "notebook:2zz45n8ip3uu68chl8qt"  # [rp] trade-vision-portfolio
    summary_lines = [f"# Trade Vision Daily Digest — {today_str}", ""]
    for ticker in ["TSLA", "MSTR", "AGQ"]:
        s = sections.get(ticker, {})
        n_recs = len(s.get("recs", []))
        summary_lines.append(f"- **{ticker}** (${s.get('spot', 0):.2f}): {n_recs} CC candidates")
        if n_recs > 0:
            top = s["recs"][0]
            summary_lines.append(
                f"  - Top: strike ${top.strike} exp {top.expiration} "
                f"delta={top.delta:.3f} premium={top.premium_pct*100:.2f}% score={top.score:.1f}"
            )
    summary_lines.append("")
    summary_lines.append(f"- **VIX**: {meta_market['vix_close']:.2f}")
    summary_lines.append(f"- **SPY**: ${meta_market['spy_close']:.2f}")
    summary = "\n".join(summary_lines)

    write_to_notebook(master_notebook, f"Trade Vision Daily Digest {today_str}", summary)
    # Also write per-stock notes
    for ticker, cfg in PORTFOLIO.items():
        s = sections.get(ticker, {})
        if s.get("recs"):
            note_lines = [
                f"# {ticker} CC candidates — {today_str}",
                "",
                f"**Spot**: ${s.get('spot', 0):.2f}",
                f"**Markov**: state={s.get('markov', {}).get('current_state', '?')}, "
                f"P(up 3d)={s.get('markov', {}).get('p_up_3d', 0):.3f}",
                "",
                "**Recommendations:**",
                "",
            ]
            from dataclasses import asdict
            for r in s["recs"][:3]:
                d = asdict(r)
                note_lines.append(
                    f"- Strike ${d['strike']} exp {d['expiration']} "
                    f"delta={d['delta']:.3f} premium={d['premium_pct']*100:.2f}% "
                    f"P(ITM)={d['p_itm_adjusted']*100:.1f}% score={d['score']:.1f}"
                )
            if cfg["is_leveraged_etf"]:
                note_lines.append("")
                note_lines.append(
                    f"⚠️ **AGQ vol-decay warning**: ~{s.get('agq_decay_wk', 0):.2%}/week "
                    f"compounding loss"
                )
            write_to_notebook(cfg["notebook_id"], f"{ticker} CC candidates {today_str}",
                              "\n".join(note_lines))

    print()
    print(f"=== Pipeline complete — see {digest_path} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())