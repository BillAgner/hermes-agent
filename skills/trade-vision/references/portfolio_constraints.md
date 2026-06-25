# Portfolio Constraints & Risk Rules

## Position limits

| Ticker | Max % of holding | Max contracts per day | Notes |
|--------|------------------|----------------------|-------|
| TSLA   | 50% of shares    | 34 contracts (1 lot = 100 shares; 6800 shares total) | High-IV, plenty of liquidity |
| MSTR   | 30% of shares    | 4 contracts (1430 shares, lot=100) | High IV → bigger premium, but wider swings |
| AGQ    | 25% of shares    | 1 contract (300 shares, lot=100; effectively 3 lots max) | Vol-decay risk caps exposure |

These are conservative starting caps. After 30 days of trade journaling, if the win-rate is >80% and zero assignments, the caps can be relaxed by 25%.

## Strike buffer rules

**Hard minimum**: 2% OTM (delta <= 0.50 for ≤7 DTE).

**Recommended**: 1.5× weekly ATR buffer. For each ticker, weekly ATR is computed as:
```
weekly_atr = 5-day ATR × sqrt(5)
```

If `(K - S) / S < 1.5 * weekly_atr / S`, the strike is rejected.

| Ticker | Typical weekly ATR | Min buffer $ | Min buffer % |
|--------|--------------------|--------------|--------------|
| TSLA   | ~$15 (~$6%)        | ~$23 above spot | ~3.5% OTM  |
| MSTR   | ~$25 (~$12%)       | ~$38 above spot | ~5% OTM   |
| AGQ    | ~$2 (~$8%)         | ~$3 above spot | ~4% OTM   |

## DTE (days to expiry) selection

| Horizon | Use case | Notes |
|---------|----------|-------|
| 0-2 DTE | Tomorrow's close, fastest theta | Only when IV-rank >50 AND no earnings in 2 days |
| 3-5 DTE | Standard weekly CC | Default for most days |
| 6-7 DTE | Friday-expiry weekly | Higher premium but more time in trade |

We screen all three buckets and recommend the best.

## Earnings & catalyst calendar

For each ticker, we maintain an upcoming-events table. Strikes that expire within 24 hours AFTER an earnings date are excluded (the move would blow through the strike).

For TSLA, MSTR, AGQ, the upcoming earnings dates are pulled from the cached fundamentals table (refreshed daily).

For TSLA specifically, additional catalysts to watch:
- Tesla delivery numbers (quarterly)
- Elon tweets / X posts (sentiment, not formal catalysts)
- FSD beta releases
- Cybertruck production updates

For MSTR:
- BTC price action (MSTR is leveraged BTC proxy)
- Michael Saylor announcements / buys
- BTC ETF flows

For AGQ:
- Silver spot price (2x leveraged)
- COMEX inventory data (already tracked by silver-comex-inventory project)
- Industrial demand news (solar, electronics)

## Risk per trade

Max loss per trade = `max(strike - spot_at_expiry, 0) * 100 shares * contracts - premium_received`

For example:
- TSLA spot = $250, strike = $260, premium = $4
- Max loss = ($260 - $250) * 100 - $400 = $600 (if assigned at $260, you "lose" the upside but kept $400)

We compute the **expected value** for each recommendation:
```
EV = P(OTM) * premium_per_share 
   + P(ITM) * (premium_per_share - (K - S_expected) + tax_adj)
```

Where `S_expected` = expected spot at expiry from MC simulation, and `tax_adj` = tax treatment of any assignment.

Only recommendations with `EV > 0` are surfaced.

## Concentration limits

No single ticker should represent >50% of the total options premium generated in a week. If TSLA generates $5,000 in weekly premium and MSTR generates $1,000, that's fine. If MSTR drops off and TSLA is the only source, we cap TSLA at $2,500/week until diversification returns.

## Crypto (sentiment-only)

SOL, HBAR, BTC are NOT in the recommendation engine. They appear only as:
1. Market-regime context in the daily market notebook
2. Correlation inputs (BTC → MSTR, SOL → general crypto risk appetite)
3. Sentiment indicators (crypto fear/greed index, BTC dominance)

If Bill later wants crypto options analysis (Deribit/CME), that's a separate module to build.

## Daily limits

Max 10 new CC recommendations per day across all tickers. If more candidates qualify, we surface the top 10 by score and note the rest in the "also considered" section.

## Trade journal discipline

Every recommendation that gets acted on must be logged in the trade journal via the dashboard form. The fields:
- ticker, strike, expiration, contracts
- premium received (per share)
- entry date, intended exit (50% decay or DTE=1, whichever first)
- actual exit date, actual exit premium
- outcome: profit / loss / assigned
- notes (optional)

This feeds back into the recommendation engine:
- If actual win-rate < 60% for a ticker, lower its position cap
- If assignment rate > 5%, raise the minimum buffer
- If exit discipline is poor (most trades held to expiry), recommend stricter exits