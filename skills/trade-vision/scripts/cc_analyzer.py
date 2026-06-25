"""
Covered-Call Scoring Engine — combines Black-Scholes, Monte Carlo, Markov,
and earnings penalty to rank candidate strikes.

Inputs: options chain + spot + markov_state + earnings_days + IV_rank
Output: ranked list of recommended strikes with score breakdown.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

import numpy as np

from options_screen import OptionContract, bs_call, norm_cdf, RFR_DEFAULT


@dataclass
class ScoredStrike:
    ticker: str
    expiration: str
    dte: int
    strike: float
    bid: float
    ask: float
    mid: float
    delta: float
    iv: float
    iv_rank: float  # 0-1 (current IV vs 52w range)
    buffer_pct: float  # (K-S)/S, positive = OTM
    buffer_atr: float  # buffer / weekly_atr_pct
    p_itm: float  # MC raw
    p_itm_adjusted: float  # after Markov + earnings penalty
    premium_pct: float  # mid / spot
    premium_per_day: float  # premium_pct / DTE
    score: float
    exit_target: float  # recommended buy-back price
    expected_profit_pct: float
    risk_flags: list[str] = field(default_factory=list)
    rationale: str = ""


def get_recent_ohlc(db_path: str, series_id: int, days: int = 60) -> tuple[list[float], float]:
    """Load last N days of closes from timeseries.db. Returns (closes, last_close)."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "SELECT close FROM ohlcv_bars WHERE series_id=? ORDER BY ts DESC LIMIT ?",
        (series_id, days),
    )
    rows = [r[0] for r in c.fetchall() if r[0] is not None]
    conn.close()
    closes = list(reversed(rows))
    return closes, closes[-1] if closes else 0.0


def compute_iv_rank(closes: list[float], current_iv: float, lookback: int = 252) -> float:
    """Compute IV-rank using realized vol as a proxy for IV range.

    IV-rank = (current - min) / (max - min) over the lookback window.
    Without historical IV data, we approximate using realized vol.
    """
    if len(closes) < lookback:
        lookback = len(closes)

    recent = closes[-lookback:]
    rets = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i - 1] > 0]

    # Rolling 20d realized vol (annualized) for each window
    window = 20
    rvol_series = []
    for i in range(window, len(rets)):
        chunk = rets[i - window : i]
        mean = sum(chunk) / len(chunk)
        var = sum((r - mean) ** 2 for r in chunk) / (len(chunk) - 1)
        rvol_series.append(math.sqrt(var) * math.sqrt(252))

    if not rvol_series:
        return 0.5

    rvol_min = min(rvol_series)
    rvol_max = max(rvol_series)
    if rvol_max == rvol_min:
        return 0.5

    # current_iv is annualized decimal; rvol series is annualized decimal
    rank = (current_iv - rvol_min) / (rvol_max - rvol_min)
    return max(0.0, min(1.0, rank))


def compute_weekly_atr_pct(closes: list[float], period: int = 5) -> float:
    """Compute weekly ATR as percentage of spot.

    Uses average true range over `period` days, then scales by sqrt(5) to weekly.
    """
    if len(closes) < period + 1:
        return 0.02  # default 2%

    # True range approximation: use close-to-close abs returns
    abs_returns = []
    for i in range(1, len(closes)):
        abs_returns.append(abs(closes[i] - closes[i - 1]))

    recent_atr_dollar = sum(abs_returns[-period:]) / period
    spot = closes[-1]
    atr_pct_daily = recent_atr_dollar / spot if spot > 0 else 0.02
    # Weekly ATR ≈ daily ATR × sqrt(5)
    return atr_pct_daily * math.sqrt(5)


def monte_carlo_itm_probability(S: float, K: float, T: float, sigma: float,
                                 drift: float = 0.0, n_paths: int = 10000,
                                 seed: int = None) -> float:
    """Estimate P(S_T >= K) via Monte Carlo simulation.

    Uses log-normal random walks. Drift in annual terms (e.g., 0.05 for 5%).
    Returns probability of ITM.
    """
    if T <= 0:
        return 1.0 if S >= K else 0.0

    rng = np.random.default_rng(seed)
    # Daily step size (assume 252 trading days per year)
    n_steps = max(1, int(T * 252))
    dt = T / n_steps
    sqrt_dt = math.sqrt(dt)

    # Generate all returns at once
    daily_returns = rng.normal(
        (drift - 0.5 * sigma * sigma) * dt,
        sigma * sqrt_dt,
        size=(n_paths, n_steps),
    )
    # Compute log-price paths
    log_paths = np.cumsum(daily_returns, axis=1)
    final_log = log_paths[:, -1]
    final_prices = S * np.exp(final_log)

    p_itm = float(np.mean(final_prices >= K))
    return p_itm


def score_strike(
    contract: OptionContract,
    spot: float,
    iv_rank: float,
    weekly_atr_pct: float,
    markov_p_up_3d: float,
    earnings_days: Optional[int],
    ticker: str,
    is_leveraged_etf: bool = False,
    agq_decay_per_week: float = 0.0,
    db_path: str = r"C:/Data/Hermes/finance/data/timeseries.db",
) -> Optional[ScoredStrike]:
    """Score a single strike. Returns None if it fails hard filters.

    Hard filters:
    - Strike must be OTM by at least 2% AND 1.5x weekly ATR
    - Raw P(ITM) must be <15%
    - After adjustments, P(ITM) must be <20%
    """
    K = contract.strike
    mid = contract.mid

    # 1. Distance buffer
    buffer_pct = (K - spot) / spot if spot > 0 else 0
    buffer_atr = buffer_pct / weekly_atr_pct if weekly_atr_pct > 0 else 0

    risk_flags = []

    if buffer_pct < 0.02:
        return None  # Less than 2% OTM
    if buffer_atr < 1.5:
        return None  # Less than 1.5x weekly ATR buffer

    # 2. MC probability of ITM
    exp_dt = datetime.fromisoformat(contract.expiration).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    T = max(0.001, (exp_dt - now).total_seconds() / (365.0 * 24 * 3600))
    dte = max(0, int((exp_dt.date() - now.date()).days))

    sigma = contract.iv if contract.iv > 0 else 0.30  # fallback
    p_itm_raw = monte_carlo_itm_probability(spot, K, T, sigma, n_paths=10000)

    if p_itm_raw > 0.15:
        return None  # Too risky raw

    # 3. Markov adjustment (penalize ITM prob if trend is up)
    # P_ITM_adjusted = P_ITM * (1 + 0.20 * (P(up) - 0.50))
    markov_adj = 1 + 0.20 * (markov_p_up_3d - 0.50)
    p_itm_adj = p_itm_raw * markov_adj

    # 4. Earnings penalty
    if earnings_days is not None and 0 <= earnings_days < dte:
        # Earnings happens before expiry — penalty scales as it gets closer
        penalty = max(0, (dte - earnings_days)) * 0.10  # up to ~50% if same-day
        p_itm_adj *= 1 + penalty
        risk_flags.append(f"earnings_in_{earnings_days}d")

    if p_itm_adj > 0.20:
        return None  # Too risky after adjustments

    # 5. Spread check (skip if bid-ask spread > 20%)
    if contract.spread_pct > 0.20:
        risk_flags.append("wide_spread")

    # 6. Premium quality
    premium_pct = mid / spot if spot > 0 else 0
    if dte > 0:
        premium_per_day = premium_pct / dte
    else:
        premium_per_day = premium_pct

    # 7. AGQ-specific decay check
    net_premium = premium_pct
    if is_leveraged_etf:
        weekly_decay_pct = agq_decay_per_week
        # Premium per week = premium_pct * (7/dte)
        premium_per_week = premium_pct * (7 / max(dte, 1))
        net_premium = premium_per_week - weekly_decay_pct
        if net_premium <= 0:
            risk_flags.append(f"net_negative_after_decay (prem/wk={premium_per_week:.3%} vs decay/wk={weekly_decay_pct:.3%})")
            return None  # Skip net-negative trades

    # 8. Composite score
    # Components, each scaled to roughly [0, 100]:
    # - Premium quality (40%): higher premium-per-week is better
    # - Risk (30%): lower P(ITM) is better
    # - Buffer (20%): sweet spot is 1.5x-3x weekly ATR (too little = risky, too much = wasteful)
    # - IV-rank (10%): lower IV-rank means premium is undervalued (mean-reversion edge)
    premium_pw = premium_pct * (7 / max(dte, 1))  # premium per week
    premium_score = min(premium_pw * 1000, 100)  # cap at 10% per week = 100
    risk_score = (1 - p_itm_adj) * 100
    # Buffer score: peaks at 2.5x ATR, decays outside [1.5, 4.0]
    if buffer_atr < 1.5:
        buffer_score = 0
    elif buffer_atr <= 2.5:
        buffer_score = (buffer_atr - 1.5) / 1.0 * 100  # ramps 0->100
    elif buffer_atr <= 4.0:
        buffer_score = 100 - (buffer_atr - 2.5) / 1.5 * 30  # slowly decays 100->70
    else:
        buffer_score = 0  # too far OTM, premium wasted
    iv_rank_score = (1 - iv_rank) * 100

    score = (
        0.40 * premium_score
        + 0.30 * risk_score
        + 0.20 * buffer_score
        + 0.10 * iv_rank_score
    )

    # Floor: require minimum premium-per-week of 0.15% for a viable CC
    if premium_pw < 0.0015:
        return None  # Premium too small to be worth selling

    # Exit strategy: close at 50% of original credit
    exit_target = round(mid * 0.50, 2)
    expected_profit_pct = (mid - exit_target) / spot

    rationale = (
        f"{buffer_atr:.1f}x ATR buffer, raw P(ITM)={p_itm_raw:.1%}, "
        f"adj P(ITM)={p_itm_adj:.1%}, premium/wk={premium_pct * (7/max(dte,1)):.2%}, "
        f"delta={contract.delta:.2f}"
    )

    return ScoredStrike(
        ticker=ticker,
        expiration=contract.expiration,
        dte=dte,
        strike=K,
        bid=contract.bid,
        ask=contract.ask,
        mid=mid,
        delta=contract.delta,
        iv=contract.iv,
        iv_rank=round(iv_rank, 3),
        buffer_pct=round(buffer_pct, 4),
        buffer_atr=round(buffer_atr, 2),
        p_itm=round(p_itm_raw, 4),
        p_itm_adjusted=round(p_itm_adj, 4),
        premium_pct=round(premium_pct, 4),
        premium_per_day=round(premium_per_day, 5),
        score=round(score, 2),
        exit_target=exit_target,
        expected_profit_pct=round(expected_profit_pct, 4),
        risk_flags=risk_flags,
        rationale=rationale,
    )


def score_chain(
    ticker: str,
    calls: list[OptionContract],
    spot: float,
    markov_p_up_3d: float,
    earnings_days: Optional[int] = None,
    is_leveraged_etf: bool = False,
    agq_decay_per_week: float = 0.0,
    db_path: str = r"C:/Data/Hermes/finance/data/timeseries.db",
    series_id: int = None,
    max_recommendations: int = 3,
) -> list[ScoredStrike]:
    """Score all strikes in a chain and return top recommendations."""
    if spot <= 0 or not calls:
        return []

    # Compute IV-rank + weekly ATR from recent prices
    closes = []
    if series_id:
        closes, _ = get_recent_ohlc(db_path, series_id, days=60)

    # Get current IV from the chain (median across ATM-ish strikes)
    ivs = [c.iv for c in calls if 0.05 < c.iv < 3.0]
    current_iv = float(np.median(ivs)) if ivs else 0.30

    iv_rank = compute_iv_rank(closes, current_iv) if closes else 0.5
    weekly_atr_pct = compute_weekly_atr_pct(closes) if closes else 0.02

    scored = []
    for c in calls:
        s = score_strike(
            contract=c,
            spot=spot,
            iv_rank=iv_rank,
            weekly_atr_pct=weekly_atr_pct,
            markov_p_up_3d=markov_p_up_3d,
            earnings_days=earnings_days,
            ticker=ticker,
            is_leveraged_etf=is_leveraged_etf,
            agq_decay_per_week=agq_decay_per_week,
        )
        if s is not None:
            scored.append(s)

    # Sort by score descending
    scored.sort(key=lambda x: x.score, reverse=True)

    return scored[:max_recommendations]


# ----------------------------- AGQ decay estimator -----------------------------

def estimate_agq_decay(closes: list[float]) -> float:
    """Estimate weekly volatility decay for AGQ based on silver's realized vol.

    Empirical: AGQ loses ~0.5%/week at low vol (RV<15%), ~1.5%/wk medium,
    ~3%/wk high (RV>30%). Use square-root scaling as approximation.
    """
    if len(closes) < 30:
        return 0.01

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    window = 20
    chunk = rets[-window:]
    mean = sum(chunk) / len(chunk)
    var = sum((r - mean) ** 2 for r in chunk) / (len(chunk) - 1)
    rv_annual = math.sqrt(var) * math.sqrt(252)

    # Map RV to decay rate (per-week)
    # RV 15% -> 0.5%/wk, RV 30% -> 1.5%/wk, RV 50% -> 3%/wk
    if rv_annual < 0.15:
        return 0.005
    if rv_annual < 0.30:
        # linear interpolation
        return 0.005 + (rv_annual - 0.15) / 0.15 * 0.010
    if rv_annual < 0.50:
        return 0.015 + (rv_annual - 0.30) / 0.20 * 0.015
    return 0.030


def main():
    """CLI: score options chain for a ticker."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Score CC candidates from options chain")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument("--min-dte", type=int, default=0)
    parser.add_argument("--max-dte", type=int, default=7)
    parser.add_argument("--prefer", default="auto")
    parser.add_argument("--db", default=r"C:/Data/Hermes/finance/data/timeseries.db")
    parser.add_argument("--series-id", type=int, default=None)
    parser.add_argument("--earnings-days", type=int, default=None)
    parser.add_argument("--is-leveraged-etf", action="store_true")
    args = parser.parse_args()

    # Lazy imports to avoid circular dependency
    from options_screen import screen

    calls, spot, meta = screen(args.ticker, args.min_dte, args.max_dte, args.prefer)
    print(f"Fetched {len(calls)} strikes from {meta.get('source')} (spot=${spot:.2f})")

    # Default series-id mapping
    series_map = {"TSLA": 3, "MSTR": 4, "AGQ": 5}
    series_id = args.series_id or series_map.get(args.ticker.upper())

    # Get Markov
    from markov_matrix import compute_markov
    markov_state = compute_markov(args.ticker, args.db, series_id) if series_id else None
    markov_p_up = markov_state.p_up_3d if markov_state else 0.5

    # AGQ decay
    agq_decay = 0.0
    if args.is_leveraged_etf and series_id:
        closes, _ = get_recent_ohlc(args.db, series_id, days=60)
        agq_decay = estimate_agq_decay(closes)

    # Score
    scored = score_chain(
        ticker=args.ticker.upper(),
        calls=calls,
        spot=spot,
        markov_p_up_3d=markov_p_up,
        earnings_days=args.earnings_days,
        is_leveraged_etf=args.is_leveraged_etf,
        agq_decay_per_week=agq_decay,
        db_path=args.db,
        series_id=series_id,
    )

    print(f"\nMarkov P(up 3d): {markov_p_up:.3f}")
    if args.is_leveraged_etf:
        print(f"AGQ decay/wk estimate: {agq_decay:.3%}")
    print(f"\nTop {len(scored)} recommendations:")
    print(f"{'Strike':>8} {'Exp':>12} {'DTE':>4} {'Δ':>6} {'Buffer':>7} {'P(ITM)':>7} {'AdjP':>7} {'Premium':>8} {'Score':>6} {'Flags':>20}")
    for s in scored:
        flags_str = ",".join(s.risk_flags) if s.risk_flags else ""
        print(f"{s.strike:>8.2f} {s.expiration:>12} {s.dte:>4d} {s.delta:>6.3f} {s.buffer_atr:>6.2f}x {s.p_itm*100:>6.1f}% {s.p_itm_adjusted*100:>6.1f}% {s.premium_pct*100:>7.2f}% {s.score:>6.1f} {flags_str:>20}")

    # Save
    out_dir = Path(r"C:/Data/Hermes/skills/trade-vision/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"cc_recs_{args.ticker.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump({
            "ticker": args.ticker.upper(),
            "spot": spot,
            "meta": meta,
            "markov_p_up_3d": markov_p_up,
            "agq_decay_per_week": agq_decay,
            "recommendations": [asdict(s) for s in scored],
        }, f, indent=2, default=str)
    print(f"\nSaved to {out_file}")


if __name__ == "__main__":
    main()