# Recommended alert thresholds

The default alert condition is `registered_ratio < threshold`, where `registered_ratio = registered_oz / (registered_oz + eligible_oz)`. Lower = tighter physical supply = more squeeze potential.

## Silver

| Threshold | Meaning | Action |
|---|---|---|
| **0.30** | Watch zone — registered share falling fast | Add to a watch list, no action |
| **0.25** | **Squeeze precursor** (Bill's setting) | Review positions, prep alert, consider scaling into physical or PSLV |
| **0.20** | Active squeeze forming | Consider increasing physical allocation, expect price volatility |
| **0.15** | Delivery crisis territory | The 1980 / 2024-25 Hunt-era comparison |

**Why 0.25 for silver?** Historically, COMEX silver registered hovers around 30-45% of total in normal conditions. When it drops under 25%, the registered stock is too thin to back the open interest comfortably — i.e., more contracts exist than there is deliverable metal, even if most of those contracts are cash-settled. Below 0.25 is when paper shorts start getting nervous and the curve stays in persistent backwardation.

## Gold

| Threshold | Meaning | Action |
|---|---|---|
| **0.55** | Watch zone | No action — gold market is more liquid, lower volatility |
| **0.50** | **Squeeze precursor** | Review; gold squeezes are rarer and tend to be geopolitical-driven (sanctions, central bank action) |
| **0.40** | Active squeeze | Increase allocation, watch for central bank / sovereign demand |
| **0.30** | Delivery crisis | The 2022-2023 London / COMEX dislocation comparable |

**Why 0.50 for gold?** Gold's market is ~10x larger by weight and ~5x more liquid. The normal registered share is 55-70%. Gold squeezes are almost always driven by *external* (geopolitical) demand spikes, not organic physical scarcity. A drop below 50% is unusual and worth watching.

## Copper

| Threshold | Meaning | Action |
|---|---|---|
| **0.25** | Watch zone | No action — copper is a structural deficit market but rarely squeezes |
| **0.20** | **Squeeze precursor** | Review; copper squeezes are usually LME-driven, not COMEX |
| **0.15** | Active squeeze | Historically rare; check for delivery disruptions |
| **0.10** | Delivery crisis | 1990s-2000s era precedent |

**Why 0.20 for copper?** Copper registered is normally 30-45% of total. Copper's physical market is genuinely tight (multi-year structural deficit), but the COMEX copper contract is small relative to LME and physical — so COMEX registered share alone is a weak signal. Cross-reference with LME warehouse data and the CFTC COT for commercials' net shorts.

## General guidance

1. **Don't fixate on a single threshold.** The trend matters more than the level. If ratio drops from 0.35 to 0.27 over 30 days, that's a stronger signal than crossing 0.25 in a single day from 0.255.
2. **Pair with COT positioning.** The CFTC disaggregated COT report (Friday 3:30pm ET) shows commercials' net short position. Rising commercial shorts against falling registered = classic squeeze setup. Falling commercials' shorts = they're covering (squeeze already happened).
3. **Watch the front month.** A 1-month delivery squeeze can flash into a 0.18 ratio just for the front contract, then revert when paper shorts roll. The total registered/total ratio is a structural signal; the front-month OI/registered is a tactical signal.
4. **Mind the "M oz" rounding.** The script computes the ratio from "M oz"-truncated numbers, so the precision is ~0.1% (see SKILL.md pitfall). When the ratio is near your threshold, treat the *lower* of (script ratio, site ratio) as the real value.
