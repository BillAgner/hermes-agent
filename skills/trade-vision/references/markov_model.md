# Markov Direction Model

## Purpose

Predict the probability that the underlying will move up, down, or stay flat over the next 1-3 trading days. Used as a directional input to the CC strike-selection algorithm — specifically as a Bayesian adjustment on the Monte Carlo ITM probability.

## Why Markov?

A first-order Markov chain on daily returns is a simple, fast, well-understood model that:
- Captures short-term momentum (up-day → up-day) and mean-reversion (down-day → up-day bounce)
- Doesn't require fitting parameters (just transition counts)
- Is interpretable: every state transition has a clear meaning

It's a WEAK predictor alone (literature suggests 52-55% directional accuracy at best). But as ONE input among several (alongside IV-rank, ATR buffer, earnings proximity), it adds edge without overfitting.

## State definition

Three states for daily log-returns:
- **UP**: `r_t > +threshold_up` (default threshold = +0.5 × daily σ)
- **FLAT**: `-threshold_down <= r_t <= +threshold_up` (default threshold = 0.5 × daily σ)
- **DOWN**: `r_t < -threshold_down`

`r_t = ln(S_t / S_{t-1})` (log-return, not arithmetic, for proper compounding)

The thresholds are dynamic — computed from rolling 20-day realized volatility:
- `threshold_up = 0.5 * σ_daily_20d`
- `threshold_down = -0.5 * σ_daily_20d`

This makes the states scale-invariant (works for AGQ at $40 and TSLA at $250 equally).

## Transition matrix

For each ticker, compute the count matrix `C[i][j]` = number of times state `i` was followed by state `j` over the last 252 trading days (1 year).

Transition probability:
```
P[i][j] = C[i][j] / sum(C[i][k] for k in [UP, FLAT, DOWN])
```

The matrix is row-stochastic: `sum(P[i]) == 1.0` for each row.

## Prediction

Given today's state `s_t`, the probability of UP tomorrow is:
```
P(up | s_t) = P[s_t][UP]
```

For multi-day prediction:
```
P(up in 2 days) = sum over intermediate states j of P[s_t][j] * P[j][UP]
```

Equivalent matrix operation:
```
P_2day = P_1day @ P_1day  # matrix multiplication
```

For 3-day horizon, cube it.

## Output schema

```python
@dataclass
class MarkovState:
    ticker: str
    as_of: date
    current_state: str  # 'UP' | 'FLAT' | 'DOWN'
    matrix: dict  # {from_state: {to_state: prob}}
    p_up_1d: float
    p_up_2d: float
    p_up_3d: float
    confidence: float  # sample-size-weighted (more data = higher confidence)
```

## Validation

Walk-forward validation:
1. For each day in the test window, use only data BEFORE that day to compute the matrix
2. Predict P(up) for that day
3. Compare to actual outcome
4. Aggregate: hit rate, Brier score, calibration

If hit rate < 50%, the model is worse than coin-flip — disable it for that ticker.

## Limitations

- **Regime changes**: the matrix is a summary statistic. If the market regime shifts (e.g., vol compression → expansion), the historical matrix is misleading.
- **Sample size**: 252 days gives noisy estimates for FLAT-state transitions (less common).
- **Symmetry assumption**: assumes transition probabilities are time-invariant (clearly false).

Mitigations:
- Use a 60-day rolling window (more responsive to regime changes) instead of full-year
- Add exponential weighting on recent transitions (e.g., 0.95 decay per day)
- Combine with IV-rank + ATR buffer — don't rely on Markov alone

## When NOT to use it

- During the 3 days before earnings (Markov history is dominated by non-earnings regimes)
- For tickers with < 60 days of price history (insufficient sample)
- For crypto on weekend gaps (24/7 trading breaks the daily-cadence assumption)

## Pine Script visualization

Output on chart:
- Table in top-right showing `P(up 1d)`, `P(up 2d)`, `P(up 3d)` with arrows
- Background color tint: green if `P(up 1d) > 0.55`, red if `< 0.45`, gray otherwise
- Horizontal lines at recent support/resistance levels (computed separately, not Markov)
- Plot of Markov state as colored candles (UP=green, FLAT=gray, DOWN=red)

See `pine_scripts/Markov_Direction.pine` for the implementation.