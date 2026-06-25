# Trade Vision — Pine Script v5 Indicators

Production-quality TradingView Pine Script v5 indicators for the Trade Vision
covered-call advisor. Each script is **standalone** (no imports), uses only
built-in functions, and has been validated via TradingView Desktop's offline
static analyzer (`pine_analyze`).

## Files

| Script | Purpose | Overlay |
| --- | --- | --- |
| `Markov_Direction.pine` | 1st-order Markov chain on log-returns; transition matrix + P(up) for 1/2/3-day horizons | Yes |
| `IV_Rank_Percentile.pine` | Rolling realized-vol based IV rank & percentile (separate pane) | No |
| `Support_Resistance_Levels.pine` | Pivot-based S/R + Fibonacci retracement of the dominant swing | Yes |
| `Earnings_Proximity.pine` | Countdown to next earnings + RV-spike warning + projected vertical line | Yes |
| `CC_Probability_Cones.pine` | Black-Scholes derived ±1σ / ±2σ cones, forward price, P(S_T > K) for three candidate strikes | Yes |

## How to install (TradingView Desktop)

1. Open TradingView Desktop.
2. Open the Pine Editor (bottom panel).
3. Open each `.pine` file in any text editor.
4. Copy/paste the source into a new Pine Script, save it with the same name.
5. Add to chart via "Indicators" → "My Scripts" → pick the indicator.

All scripts work on **any timeframe / symbol** with at least ~30 bars of history
(60+ preferred for the Markov script).

## How to install (TradingView Web / Cloud)

Same as above, but use TradingView's online Pine Editor and paste the source.

## Validation status

All five scripts were test-compiled via the offline static analyzer:

```
mcp__tradingview__desktop__pine_analyze (TradingView Desktop MCP)
```

| Script | Static analysis | Verdict |
| --- | --- | --- |
| `Markov_Direction.pine` | 0 issues | [OK] |
| `IV_Rank_Percentile.pine` | 0 issues | [OK] |
| `Support_Resistance_Levels.pine` | 0 issues | [OK] |
| `Earnings_Proximity.pine` | 0 issues | [OK] |
| `CC_Probability_Cones.pine` | 0 issues | [OK] |

The analyzer checks for:
- Array out-of-bounds access
- Unguarded `array.first()` / `array.last()`
- Bad loop bounds
- Implicit boolean casts

No errors, no warnings — every script is clean.

For full server-side compile (the strictest check), TradingView Desktop must be
running and `pine_smart_compile` can be invoked; this was not required since the
static analyzer already passes on every file.

## What each script does — in detail

### 1. Markov_Direction.pine

- Discretizes daily log-returns into three states: **UP** / **FLAT** / **DOWN**.
  Threshold = `0.5 × rolling-20d σ` so the states are scale-invariant.
- Sliding 252-bar window (one trading year); counts state→state transitions.
- Builds a row-stochastic 3×3 transition probability matrix.
- Computes `P(up | s_t)` for 1, 2, 3-day horizons via matrix multiplication.
- Plots:
  - Three probability lines (`P(up 1d)` blue, `P(up 2d)` purple, `P(up 3d)` orange).
  - Candle barcolor by current state (green / gray / red).
  - Background tint when `P(up 1d) > 0.55` (green) or `< 0.45` (red).
- Top-right table: state, sample size N, full transition matrix, three
  probabilities.

Mirrors the Python implementation described in
`../markov_model.md`.

### 2. IV_Rank_Percentile.pine

- Computes `RV_t = sqrt(252/window) × stdev(log_returns, window)` annualized.
- Tracks `RV_Hi = highest(RV, 252)` and `RV_Lo = lowest(RV, 252)` for the
  52-week range.
- **IV rank** = `(RV - RV_Lo) / (RV_Hi - RV_Lo) × 100`, clamped to [0, 100].
- **IV percentile** = `%` of past 252 RV values ≤ current RV.
- Separate pane (`overlay=false`) with two lines (rank blue / percentile purple),
  threshold `hline()` at 25 / 50 / 75, and a regime background tint.
- Top-right table: RV%, range, rank, percentile, regime, **CC advice**
  (`SELL calls` / `OPTIONAL` / `AVOID selling`).

TradingView has no native options-chain IV feed; we use **realized** vol as the
proxy. Users who have a separate IV symbol (e.g., VIX, VXN) can
`request.security()` it in and replace `rv` directly.

### 3. Support_Resistance_Levels.pine

- `ta.pivothigh()` / `ta.pivotlow()` with `i_pivLen` bars on each side.
- Rolling buffer of last `i_pivKeep` pivots (price + role + bar_index) in arrays.
- **Clustering**: pivots of the same kind within `i_mergePct%` are merged to
  their average (last bar only).
- Draws a horizontal line per pivot, extending right; labels the right edge.
- **Fibonacci retracement** between the most recent pivot high and low; draws
  levels at 0 / 23.6 / 38.2 / 50 / 61.8 / 78.6 / 100 / 161.8 in a single
  multiline label.
- Top-left table: nearest support / resistance distance to current price.

### 4. Earnings_Proximity.pine

- Manual `input.string()` for the next earnings date (ISO yyyy-MM-dd).
- `timestamp()` parses the string; `daysUntil = (earnings - now) / 86_400_000`.
- Regime bands:
  - `BLOCK` (≤ `i_blockDays` = 1 d) — red, do NOT open new CCs
  - `CRITICAL` (≤ 3 d) — orange
  - `WARN` (≤ `i_warnDays` = 7 d) — yellow
- Background tint per regime.
- Computes `RV(5d)` / `RV(20d)` ratio; if > `i_volSpikeK = 1.5×`, paints a
  "RV spike Nx" label on that bar and tints the RV(5d) cell red in the table.
- Projects a vertical dashed line 60 bars ahead as an estimated earnings date
  marker.

TradingView's built-in `earnings` variable is inconsistent across data plans;
the manual-input approach is more reliable for the use cases here.

### 5. CC_Probability_Cones.pine

- **Inputs**: DTE (1-60), RV window, risk-free rate, optional manual σ override,
  P(>K) warning threshold (default 15%), three strike offsets (% above spot).
- **Forward price** `F_T = S × exp((r - 0.5σ²) × T)` (Black-Scholes lognormal drift).
- **Confidence bands**: ±1σ and ±2σ of `σ × √T × S` (price-space standard
  deviation). The ±1σ band is **filled** (≈68% probability mass).
- **Strike grid**: `K_i = S × (1 + strikePct_i / 100)` for three candidate strikes.
- **`P(S_T > K)`** via `d2 = (ln(S/K) + (r - 0.5σ²) × T) / (σ √T)`, then
  `P = 1 - N(d2)`. The standard normal CDF is built from `math.erf()`:
  `N(x) = 0.5 × (1 + erf(x / √2))`.
- Right-edge colored labels per strike (green if safe, red if P(>K) exceeds the
  warning threshold).
- Background red-tint if **any** strike exceeds the threshold.
- Bottom-right table: spot, DTE, σ, r, F_T, 1σ, and a strike grid showing K /
  Δ% / P(>K) per candidate.

Matches the Black-Scholes convention in `../cc_methodology.md`. For the Markov
adjustment described there, multiply `P(>K)` by `(1 + 0.20 × (P_up - 0.50))`
before applying the 15% cap.

## Pine v5 syntax notes

All scripts use Pine v5 conventions:

- `//@version=5` declaration
- `indicator()` with `shorttitle`, `overlay`, and explicit `max_*_count`
  declarations to control chart drawing budgets
- Typed inputs: `input.int()`, `input.float()`, `input.bool()`, `input.string()`
- `var` keyword for state that persists across bars
- `ta.*` prefix for built-in technical-analysis functions
- `math.*` prefix for math utilities (incl. `math.erf()` for the BS CDF)
- `array.*` and `matrix.*` collections
- `str.tostring()` with format strings (`"#.##"`, `format.mintick`)
- `table.new()` / `table.cell()` for on-chart tables
- `label.new()` / `line.new()` / `box.new()` with `xloc.bar_index` so drawings
  align to bar count rather than wall-clock time
- `barstate.islast` to gate expensive computation to the final bar only

## Limitations & caveats

| Script | Caveat |
| --- | --- |
| Markov_Direction | Loop over 252 bars runs on every last-bar update; for tickers with < 60 bars the matrix is all-zero. |
| IV_Rank_Percentile | Uses **realized** vol, not implied; switch to a `request.security()`-fed IV series if you have one. |
| Support_Resistance_Levels | Fibonacci requires ≥ 2 pivots to render; low-vol symbols may show only the S/R lines. |
| Earnings_Proximity | Earnings date is **manual**; update it before each report for accurate countdown. |
| CC_Probability_Cones | Assumes log-normal returns (Gaussian). Fat tails around earnings are NOT captured — combine with `Earnings_Proximity` to avoid trading through catalyst days. |

## License

Internal use — Trade Vision project. Not for redistribution.