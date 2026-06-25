# Trade Vision — CC Methodology

## Goal

Recommend short-dated (0-7 DTE) covered calls that:
1. Capture the maximum premium income per day-of-exposure
2. Carry <15% probability of finishing in-the-money (assignment)
3. Survive the volatility-decay trap on leveraged ETFs (AGQ specifically)

## Core pricing models

### Black-Scholes (baseline)

```
C = S*N(d1) - K*e^(-rT)*N(d2)

d1 = (ln(S/K) + (r + σ²/2)*T) / (σ*sqrt(T))
d2 = d1 - σ*sqrt(T)
```

Parameters:
- `S` = underlying spot (last close)
- `K` = strike
- `T` = time to expiry in years (calendar-days/365 — not trading days)
- `σ` = annualized IV (from options chain mid)
- `r` = risk-free rate (use 5% as default; pull from FRED if available)
- `N()` = standard normal CDF

### Monte Carlo probability of ITM

For each strike K:
1. Pull recent 60-day realized volatility as `σ_hist`
2. Compute drift from 5-day EMA of returns
3. Run 10,000 paths over `T` days with σ_hist
4. Count paths where `S_T >= K` → that's the empirical ITM probability
5. If ITM probability > 15%, discard the strike

Why MC in addition to BS?
- BS uses implied vol (forward-looking), MC uses realized vol (backward-looking). They diverge when there's a vol regime change.
- MC lets us include jumps (fat-tail events) by sampling from a Student-t distribution instead of Gaussian.

### Markov direction adjustment

A first-order Markov chain on daily returns gives:
- `P(up | up)` = conditional probability of an up-day following an up-day
- `P(up | down)` = conditional probability of bounce
- `P(up | flat)` = ...

We compute the **Markov-adjusted strike probability**:
```
P_ITM_adjusted = P_ITM_MC * (1 + 0.20 * (P(up) - 0.50))
```

If the chain says today is biased up (P(up) > 0.50), we apply a 20% penalty to the ITM probability — i.e., we're MORE conservative about selling calls. This protects against the regime where the trend is up.

### Earnings / catalyst penalty

If next earnings is within `T` days:
```
P_ITM_adjusted *= 1 + (T - earnings_days) * 0.10  # up to +50% for same-day
```

Earnings moves routinely exceed 5-8% on these tickers. If the CC expires the day AFTER earnings, the move could blow through the strike.

## Strike selection algorithm

Given the options chain:

```python
def score_strike(chain_entry, S, T, iv_rank, markov_up_prob, earnings_days):
    K = chain_entry['strike']
    bid = chain_entry['bid']
    ask = chain_entry['ask']
    iv = chain_entry['implied_iv']
    delta = chain_entry['delta']
    
    # 1. Distance buffer (in ATR units)
    atr_pct = 0.02  # 2% of spot as default weekly ATR proxy
    buffer_pct = (K - S) / S
    buffer_atr = buffer_pct / atr_pct
    
    if buffer_atr < 1.0:
        return None  # Strike too close
    
    # 2. MC probability of ITM
    p_itm = monte_carlo_itm_probability(S, K, T, iv)
    
    if p_itm > 0.15:
        return None  # Too risky
    
    # 3. Markov adjustment
    p_itm_adj = p_itm * (1 + 0.20 * (markov_up_prob - 0.50))
    
    # 4. Earnings penalty
    if earnings_days is not None and earnings_days < T * 365:
        p_itm_adj *= 1 + max(0, (T * 365 - earnings_days) * 0.10)
    
    if p_itm_adj > 0.20:
        return None  # Too risky after adjustments
    
    # 5. Premium quality (per-day)
    mid = (bid + ask) / 2
    days_to_exp = T * 365
    premium_per_day = mid / days_to_exp
    premium_pct_per_day = premium_per_day / S
    
    # 6. Composite score
    score = (
        0.40 * (premium_pct_per_day * 100) +           # Higher = better
        0.30 * (1 - p_itm_adj) * 100 +                 # Lower risk = better
        0.20 * (buffer_atr) * 10 +                     # More buffer = better
        0.10 * (1 - min(1.0, iv_rank)) * 100            # Lower IV-rank = better (premium undervalued)
    )
    
    return {
        'strike': K,
        'bid': bid,
        'ask': ask,
        'mid': mid,
        'delta': delta,
        'iv': iv,
        'p_itm': p_itm,
        'p_itm_adj': p_itm_adj,
        'buffer_atr': buffer_atr,
        'premium_pct_per_day': premium_pct_per_day,
        'score': score,
    }
```

## Exit strategy

**Default**: close the CC at 50% of original credit (i.e., buy back at 50% of what you sold for).

Rationale:
- Theta decay is non-linear; 50% of premium decay happens in the first ~30% of time-to-expiry for short-dated options
- Buying back at 50% locks in ~67% of max profit (sold at $1.00, bought at $0.50, kept $0.50, max profit was $1.00, captured 50/75 = 67%)
- Frees the capital for the next trade

**Alternative**: close at 25% of original credit if:
- IV has compressed >30% since entry (lock in gains before vol expands again)
- Earnings is within 24 hours

**Roll-forward**: if the trade is profitable at 50% decay but DTE > 2, consider rolling to next week's same strike. This is a manual decision.

## AGQ-specific volatility decay warning

AGQ is a 2x daily-leveraged ETF. Its daily return is:
```
AGQ_today = AGQ_yesterday * (1 + 2 * (silver_spot_today / silver_spot_yesterday - 1))
```

In a volatile market, even if silver is flat over a month, AGQ loses value due to compounding daily rebalancing losses.

**Rule**: every AGQ CC recommendation MUST include:
```
vol_decay_warning: true
expected_decay_per_week: ~X%
```

The decay rate depends on realized volatility. Empirical ranges:
- Low-vol (silver RV <15%): ~0.5% per week
- Medium-vol (RV 15-30%): ~1.5% per week
- High-vol (RV >30%): ~3% per week

The captured premium needs to EXCEED the expected decay to be net-positive. If not, the recommendation gets a `recommendation: skip` flag.

## Monte Carlo implementation note

We use Python's `random` with `random.gauss(0, σ_daily)` for 10,000 paths. For tail events we could swap to `scipy.stats.t` with `df=5` — but for 0-7 DTE, the tail risk is dominated by earnings/catalysts (already filtered separately), so Gaussian is fine.

Sample size: 10,000 paths gives ±0.5% precision on the ITM probability at the 15% threshold. If we need higher precision (e.g., for backtests), bump to 50,000.

## Validation / sanity checks

- Sum of `p_itm` + `p_otm` ≈ 1.0 (within MC noise)
- `delta ≈ p_itm` for short-dated ATM options (BS put-call parity approximation)
- Median path `S_T` ≈ `S * exp(drift * T)` (log-normal consistency)
- 95% confidence interval of `S_T` includes `S` (no drift bias in MC)