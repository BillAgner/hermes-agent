"""
Options Chain Screener — multi-backend (Tradier preferred, yfinance fallback).

Pulls live options chains for short-dated covered calls, computes Black-Scholes
greek approximations, returns a normalized list of strikes ready for the CC
scoring engine.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Optional imports — yfinance is the fallback, Tradier uses urllib
try:
    import yfinance as yf
except ImportError:
    yf = None


# Risk-free rate default (used for BS calc). Override via env or fetch live.
RFR_DEFAULT = 0.045  # ~4.5% as of mid-2026


@dataclass
class OptionContract:
    ticker: str
    expiration: str  # ISO date
    strike: float
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    iv: float  # annualized (decimal)
    in_the_money: bool
    contract_symbol: str = ""
    # Computed fields (filled by compute_greeks)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    mid: float = 0.0
    spread_pct: float = 0.0


def norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, dict]:
    """Black-Scholes call price + greeks.

    Returns (price, {delta, gamma, theta, vega}).
    T in years (calendar-days/365).
    """
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K), {"delta": 1.0 if S > K else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    delta = norm_cdf(d1)
    gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
    theta = (-S * norm_pdf(d1) * sigma / (2 * math.sqrt(T))
             - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0  # per-day
    vega = S * norm_pdf(d1) * math.sqrt(T) / 100.0  # per 1% IV change

    return price, {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def compute_fallback_iv(ticker: str, db_path: str = r"C:/Data/Hermes/finance/data/timeseries.db") -> float:
    """Compute a fallback IV from realized vol when chain IV is unreliable.

    Uses 30-day realized volatility from the time-series DB. Returns decimal
    (e.g., 0.50 = 50%).
    """
    series_map = {"TSLA": 3, "MSTR": 4, "AGQ": 5, "SOL": 6, "BTC": 7, "HBAR": 8, "SPY": 9, "QQQ": 11}
    sid = series_map.get(ticker.upper())
    if sid is None:
        return 0.40

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT close FROM ohlcv_bars WHERE series_id=? ORDER BY ts DESC LIMIT 31", (sid,))
        rows = [r[0] for r in c.fetchall() if r[0] is not None]
        conn.close()
        if len(rows) < 20:
            return 0.40
        # Use ascending order
        closes = list(reversed(rows))
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 10:
            return 0.40
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)
    except Exception:
        return 0.40


def compute_greeks(contract: OptionContract, S: float, rfr: float = RFR_DEFAULT,
                   fallback_iv: float = None, use_fallback: bool = False) -> OptionContract:
    """Compute BS greeks for an option contract given spot S.

    If use_fallback=True, ALWAYS use fallback_iv (typically realized vol from
    time-series data). yfinance IV is unreliable for short-dated strikes, so
    we default to realized vol when available.

    If use_fallback=False, only fall back when the supplied IV is implausible
    (<10% or >300%).
    """
    exp_dt = datetime.fromisoformat(contract.expiration).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    T = max(0.001, (exp_dt - now).total_seconds() / (365.0 * 24 * 3600))

    # Default: use fallback IV if available (realized vol is more reliable
    # than yfinance IV for short-dated strikes)
    if use_fallback and fallback_iv and 0.05 <= fallback_iv <= 5.0:
        iv = fallback_iv
    else:
        iv = contract.iv if contract.iv > 0 else (fallback_iv or 0.30)
        # Sanity check
        if iv < 0.10 or iv > 3.0:
            if fallback_iv and 0.10 <= fallback_iv <= 3.0:
                iv = fallback_iv
            else:
                iv = 0.40 if T < 0.05 else 0.30

    mid = (contract.bid + contract.ask) / 2 if (contract.bid + contract.ask) > 0 else contract.last
    spread_pct = (contract.ask - contract.bid) / mid if mid > 0 else 999.0

    _, greeks = bs_call(S, contract.strike, T, rfr, iv)
    contract.mid = round(mid, 4)
    contract.spread_pct = round(spread_pct, 4)
    contract.delta = round(greeks["delta"], 4)
    contract.gamma = round(greeks["gamma"], 6)
    contract.theta = round(greeks["theta"], 4)
    contract.vega = round(greeks["vega"], 4)
    contract.iv = round(iv, 4)
    return contract


# ----------------------------- Backend: yfinance -----------------------------

def screen_yfinance(ticker: str, min_dte: int = 0, max_dte: int = 7,
                    spot_override: float = None) -> tuple[list[OptionContract], float, dict]:
    """Pull short-dated options chain from yfinance. Returns (calls, spot, metadata)."""
    if yf is None:
        raise RuntimeError("yfinance not installed; pip install yfinance")

    t = yf.Ticker(ticker)
    expirations = list(t.options)
    today = datetime.now(timezone.utc).date()

    selected_exps = []
    exp_dates = {}
    for exp_str in expirations:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_dt - today).days
        if min_dte <= dte <= max_dte:
            selected_exps.append(exp_str)
            exp_dates[exp_str] = dte

    if not selected_exps:
        return [], 0.0, {"error": "no_expirations_in_range", "expirations_seen": expirations[:10]}

    # Get spot
    if spot_override and spot_override > 0:
        spot = spot_override
    else:
        try:
            info = t.fast_info or t.info
            spot = float(info.get("lastPrice") or info.get("regularMarketPrice") or info.get("previousClose") or 0)
        except Exception:
            spot = 0.0
        if spot <= 0:
            try:
                hist = t.history(period="1d")
                if not hist.empty:
                    spot = float(hist["Close"].iloc[-1])
            except Exception:
                pass

    calls: list[OptionContract] = []
    for exp_str in selected_exps:
        try:
            chain = t.option_chain(exp_str)
            df = chain.calls
            # Drop strikes with no data at all
            df = df[
                (df["bid"].fillna(0) > 0)
                | (df["ask"].fillna(0) > 0)
                | (df["lastPrice"].fillna(0) > 0)
            ]
            # Drop pathological IVs (yfinance gives >500% for illiquid strikes)
            df = df[df["impliedVolatility"].fillna(0) > 0]
            df = df[df["impliedVolatility"] < 5.0]
            # Drop deep ITM strikes
            df = df[~df["inTheMoney"]]
            df = df[df["strike"] >= spot * 0.95]
            for _, row in df.iterrows():
                iv = float(row.get("impliedVolatility", 0) or 0)
                # Sanity check: yfinance IV is unreliable for short-dated strikes
                c = OptionContract(
                    ticker=ticker.upper(),
                    expiration=exp_str,
                    strike=float(row["strike"]),
                    bid=float(row.get("bid", 0) or 0),
                    ask=float(row.get("ask", 0) or 0),
                    last=float(row.get("lastPrice", 0) or 0),
                    volume=int(row.get("volume", 0) or 0),
                    open_interest=int(row.get("openInterest", 0) or 0),
                    iv=round(iv, 4),
                    in_the_money=bool(row.get("inTheMoney", False)),
                    contract_symbol=str(row.get("contractSymbol", "")),
                )
                calls.append(c)
        except Exception as e:
            continue

    # Compute greeks for each, with fallback IV from realized vol
    fallback_iv = compute_fallback_iv(ticker, db_path=r"C:/Data/Hermes/finance/data/timeseries.db")
    # When using yfinance, default to fallback IV (realized vol) since yfinance
    # IV is unreliable for short-dated strikes. Pass use_fallback=True.
    calls = [compute_greeks(c, spot, fallback_iv=fallback_iv, use_fallback=True) for c in calls]

    meta = {
        "source": "yfinance",
        "expirations_in_range": selected_exps,
        "dte_map": exp_dates,
        "n_strikes": len(calls),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_iv": round(fallback_iv, 4),
    }
    return calls, spot, meta


# ----------------------------- Backend: Tradier -----------------------------

def screen_tradier(ticker: str, min_dte: int = 0, max_dte: int = 7,
                   spot_override: float = None) -> tuple[list[OptionContract], float, dict]:
    """Pull options from Tradier. Requires TRADIER_API_KEY env var."""
    import urllib.request
    import urllib.error

    api_key = os.environ.get("TRADIER_API_KEY")
    sandbox = os.environ.get("TRADIER_SANDBOX", "1") == "1"
    base = "https://sandbox.tradier.com" if sandbox else "https://api.tradier.com"

    if not api_key:
        raise RuntimeError("TRADIER_API_KEY not set; falling back to yfinance")

    today = datetime.now(timezone.utc).date()

    # 1. Get expirations
    req = urllib.request.Request(
        f"{base}/v1/markets/options/expirations?symbol={ticker.upper()}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        exp_data = json.loads(resp.read().decode())

    all_exps = exp_data.get("expirations", {}).get("date", [])
    if not all_exps:
        return [], 0.0, {"error": "no_expirations", "source": "tradier"}

    # Filter by DTE
    selected_exps = []
    exp_dates = {}
    for exp_str in all_exps:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_dt - today).days
        if min_dte <= dte <= max_dte:
            selected_exps.append(exp_str)
            exp_dates[exp_str] = dte

    if not selected_exps:
        return [], 0.0, {
            "error": "no_expirations_in_range",
            "expirations_seen": all_exps[:10],
            "source": "tradier",
        }

    # 2. Get spot from quote
    spot = spot_override or 0.0
    if spot <= 0:
        req = urllib.request.Request(
            f"{base}/v1/markets/quotes?symbols={ticker.upper()}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            quote_data = json.loads(resp.read().decode())
        quote = quote_data.get("quotes", {}).get("quote", {})
        spot = float(quote.get("last") or quote.get("close") or 0)

    # 3. Get chains
    calls = []
    for exp_str in selected_exps:
        req = urllib.request.Request(
            f"{base}/v1/markets/options/chains?symbol={ticker.upper()}&expiration={exp_str}&greeks=true",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            chain_data = json.loads(resp.read().decode())
        chain = chain_data.get("options", {}).get("option", [])
        if not isinstance(chain, list):
            chain = [chain]

        for opt in chain:
            if opt.get("option_type") != "call":
                continue
            greeks = opt.get("greeks", {}) or {}
            # Tradier stores IV as a percentage (e.g., 55.0 for 55%)
            raw_iv = float(greeks.get("mid_iv") or opt.get("iv", 0) or 0)
            iv = raw_iv / 100.0 if raw_iv > 5 else raw_iv
            c = OptionContract(
                ticker=ticker.upper(),
                expiration=exp_str,
                strike=float(opt["strike"]),
                bid=float(opt.get("bid", 0) or 0),
                ask=float(opt.get("ask", 0) or 0),
                last=float(opt.get("last", 0) or 0),
                volume=int(opt.get("volume", 0) or 0),
                open_interest=int(opt.get("open_interest", 0) or 0),
                iv=round(iv, 4),
                in_the_money=bool(opt.get("in_the_money", False)),
                contract_symbol=opt.get("symbol", ""),
                delta=round(float(greeks.get("delta", 0) or 0), 4),
                gamma=round(float(greeks.get("gamma", 0) or 0), 6),
                theta=round(float(greeks.get("theta", 0) or 0), 4),
                vega=round(float(greeks.get("vega", 0) or 0), 4),
            )
            c.mid = (c.bid + c.ask) / 2 if (c.bid + c.ask) > 0 else c.last
            c.spread_pct = (c.ask - c.bid) / c.mid if c.mid > 0 else 999.0
            calls.append(c)

    meta = {
        "source": "tradier",
        "sandbox": sandbox,
        "expirations_in_range": selected_exps,
        "dte_map": exp_dates,
        "n_strikes": len(calls),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return calls, spot, meta


# ----------------------------- Unified entry point -----------------------------

def screen(ticker: str, min_dte: int = 0, max_dte: int = 7,
           prefer: str = "auto", spot_override: float = None) -> tuple[list[OptionContract], float, dict]:
    """Screen options chains. `prefer` = 'tradier', 'yfinance', or 'auto'."""
    if prefer == "auto":
        if os.environ.get("TRADIER_API_KEY"):
            prefer = "tradier"
        else:
            prefer = "yfinance"

    if prefer == "tradier":
        try:
            return screen_tradier(ticker, min_dte, max_dte, spot_override)
        except Exception as e:
            # Fallback to yfinance
            return screen_yfinance(ticker, min_dte, max_dte, spot_override)

    return screen_yfinance(ticker, min_dte, max_dte, spot_override)


def main():
    """CLI: pull options chain for a ticker and print summary."""
    import argparse

    parser = argparse.ArgumentParser(description="Pull options chain for short-dated CCs")
    parser.add_argument("ticker", help="Ticker symbol, e.g. TSLA")
    parser.add_argument("--min-dte", type=int, default=0)
    parser.add_argument("--max-dte", type=int, default=7)
    parser.add_argument("--prefer", default="auto", choices=["auto", "tradier", "yfinance"])
    parser.add_argument("--spot", type=float, default=None)
    parser.add_argument("--save", action="store_true", help="Save raw chain to data dir")
    args = parser.parse_args()

    calls, spot, meta = screen(args.ticker, args.min_dte, args.max_dte, args.prefer, args.spot)

    print(f"Ticker: {args.ticker} | Spot: ${spot:.2f} | Source: {meta.get('source')}")
    print(f"Expirations in range ({args.min_dte}-{args.max_dte} DTE): {len(meta.get('expirations_in_range', []))}")
    print(f"Total strikes: {len(calls)}")
    if meta.get("fallback_iv"):
        print(f"Fallback IV (realized vol): {meta['fallback_iv']*100:.1f}%")
    print()
    # Top 10 by open interest
    calls.sort(key=lambda c: c.open_interest, reverse=True)
    print(f"{'Strike':>8} {'Exp':>12} {'DTE':>4} {'Bid':>7} {'Ask':>7} {'Mid':>7} {'Vol':>6} {'OI':>6} {'IV%':>6} {'Δ':>6} {'Spread%':>8}")
    for c in calls[:20]:
        exp_dt = datetime.fromisoformat(c.expiration).date()
        dte = (exp_dt - datetime.now(timezone.utc).date()).days
        print(f"{c.strike:>8.2f} {c.expiration:>12} {dte:>4d} {c.bid:>7.2f} {c.ask:>7.2f} {c.mid:>7.2f} {c.volume:>6d} {c.open_interest:>6d} {c.iv*100:>6.1f} {c.delta:>6.3f} {c.spread_pct*100:>7.1f}%")

    if args.save:
        out_dir = Path(r"C:/Data/Hermes/skills/trade-vision/data")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"chain_{args.ticker.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(out_file, "w") as f:
            json.dump({
                "ticker": args.ticker,
                "spot": spot,
                "meta": meta,
                "calls": [asdict(c) for c in calls],
            }, f, indent=2, default=str)
        print(f"\nSaved to {out_file}")


if __name__ == "__main__":
    main()