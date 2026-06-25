"""
Markov Direction Model — first-order Markov chain on daily log-returns.

Computes transition probability matrix over UP/FLAT/DOWN states for each
ticker, then forecasts P(up) over 1d/2d/3d horizons.

States are defined by dynamic thresholds (0.5 * rolling 20d realized vol)
so the model is scale-invariant across price levels.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# State constants
UP = "UP"
FLAT = "FLAT"
DOWN = "DOWN"
STATES = [UP, FLAT, DOWN]


@dataclass
class MarkovState:
    ticker: str
    as_of: str  # ISO date
    current_state: str
    matrix: dict  # {from_state: {to_state: prob}}
    p_up_1d: float
    p_up_2d: float
    p_up_3d: float
    confidence: float  # 0-1, sample-size-weighted
    n_transitions: int


def load_closes(db_path: str, series_id: int) -> tuple[list[str], list[float]]:
    """Load (timestamp, close) pairs ascending from timeseries.db."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "SELECT ts, close FROM ohlcv_bars WHERE series_id=? ORDER BY ts ASC",
        (series_id,),
    )
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows], [float(r[1]) for r in rows if r[1] is not None]


def daily_log_returns(closes: list[float]) -> list[float]:
    """Compute log-returns: ln(S_t / S_{t-1}). Drops first element."""
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]


def rolling_realized_vol(returns: list[float], window: int = 20) -> list[float]:
    """Rolling realized vol (annualized) for each day. Uses past `window` returns."""
    out = []
    for i in range(len(returns)):
        start = max(0, i - window + 1)
        chunk = returns[start : i + 1]
        if len(chunk) < 5:  # too little data
            out.append(float("nan"))
            continue
        # Annualized vol = stdev * sqrt(252)
        mean = sum(chunk) / len(chunk)
        var = sum((r - mean) ** 2 for r in chunk) / max(1, len(chunk) - 1)
        out.append(math.sqrt(var) * math.sqrt(252))
    return out


def classify_state(r: float, daily_threshold: float) -> str:
    """Classify a return into UP/FLAT/DOWN given today's daily threshold."""
    if r > daily_threshold:
        return UP
    if r < -daily_threshold:
        return DOWN
    return FLAT


def compute_markov(
    ticker: str,
    db_path: str,
    series_id: int,
    lookback_days: int = 252,
    threshold_mult: float = 0.5,
) -> MarkovState | None:
    """Compute Markov chain for `ticker` over the last `lookback_days` returns.

    Returns None if insufficient data (< 60 days).
    """
    _, closes = load_closes(db_path, series_id)
    if len(closes) < 60:
        return None

    rets = daily_log_returns(closes)
    # Take the last `lookback_days + 1` closes (= `lookback_days` returns)
    if len(rets) > lookback_days:
        rets = rets[-lookback_days:]

    # Rolling 20d realized vol -> daily threshold
    # The threshold is computed for the day BEFORE the return we're classifying
    # (i.e., uses information available at that time)
    rvol = rolling_realized_vol(rets, window=20)
    # Daily threshold = threshold_mult * sqrt(rvol_annualized^2 / 252) = threshold_mult * rvol / sqrt(252)
    daily_thresholds = [threshold_mult * v / math.sqrt(252) if v > 0 else 0.005 for v in rvol]

    # Classify each return into a state
    states = []
    # We need to skip the first 20 because rvol window needs warm-up
    start_idx = 20
    for i in range(start_idx, len(rets)):
        thresh = daily_thresholds[i - 1] if i - 1 >= 0 else 0.005  # use prev day's threshold
        states.append(classify_state(rets[i], thresh))

    if len(states) < 30:
        return None

    # Count transitions
    counts = {s: {t: 0 for t in STATES} for s in STATES}
    for i in range(len(states) - 1):
        counts[states[i]][states[i + 1]] += 1

    # Convert to probabilities (Laplace smoothing: +1 to each cell)
    matrix = {}
    for s in STATES:
        total = sum(counts[s].values()) + len(STATES)  # +3 for smoothing
        matrix[s] = {t: (counts[s][t] + 1) / total for t in STATES}

    current_state = states[-1]

    # Forecast P(up) at 1d, 2d, 3d
    p_up_1d = matrix[current_state][UP]
    # P(up in 2d) = sum over intermediate states j of P[s_t][j] * P[j][UP]
    p_up_2d = sum(matrix[current_state][j] * matrix[j][UP] for j in STATES)
    p_up_3d = sum(
        matrix[current_state][j] * matrix[j][k] * matrix[k][UP]
        for j in STATES
        for k in STATES
    )

    # Confidence: based on sample size and recency
    n_transitions = len(states) - 1
    # 252 days = full confidence; <60 days = low confidence
    confidence = min(1.0, n_transitions / 200.0)

    as_of = datetime.now(timezone.utc).isoformat()
    return MarkovState(
        ticker=ticker,
        as_of=as_of,
        current_state=current_state,
        matrix=matrix,
        p_up_1d=round(p_up_1d, 4),
        p_up_2d=round(p_up_2d, 4),
        p_up_3d=round(p_up_3d, 4),
        confidence=round(confidence, 3),
        n_transitions=n_transitions,
    )


def main():
    """CLI: compute Markov for all trade-vision-portfolio series."""
    import argparse

    parser = argparse.ArgumentParser(description="Compute Markov direction model")
    parser.add_argument("--db", default=r"C:/Data/Hermes/finance/data/timeseries.db")
    parser.add_argument("--tickers", nargs="+", default=["TSLA", "MSTR", "AGQ", "SPY", "QQQ", "VIX"])
    parser.add_argument("--series-map", default=None,
                        help="JSON file mapping ticker -> series_id; uses default map if omitted")
    args = parser.parse_args()

    # Default series map (matches what we registered in time-series)
    default_map = {
        "TSLA": 3, "MSTR": 4, "AGQ": 5,
        "SOL": 6, "BTC": 7, "HBAR": 8,
        "SPY": 9, "VIX": 10, "QQQ": 11,
    }
    if args.series_map:
        import json
        with open(args.series_map) as f:
            series_map = json.load(f)
    else:
        series_map = default_map

    results = {}
    for ticker in args.tickers:
        sid = series_map.get(ticker.upper())
        if sid is None:
            print(f"[SKIP] {ticker}: no series id")
            continue
        state = compute_markov(ticker, args.db, sid)
        if state is None:
            print(f"[SKIP] {ticker}: insufficient data")
            continue
        results[ticker] = asdict(state)
        print(f"[OK]   {ticker}: state={state.current_state} "
              f"P(up 1d)={state.p_up_1d:.3f} 2d={state.p_up_2d:.3f} 3d={state.p_up_3d:.3f} "
              f"conf={state.confidence:.2f}")

    # Save results
    out_dir = Path(r"C:/Data/Hermes/skills/trade-vision/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "markov_latest.json"
    import json
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[OK] saved to {out_file}")


if __name__ == "__main__":
    main()