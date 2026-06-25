# Scoring examples — what the formula produces

These are the scores you should expect from the scorer for common patterns. If
real-world scores diverge significantly, that's the signal to update either the
domain tier table or the heuristic itself.

## Tier 1 — primary data

| Source | Class | Score | Action |
|---|---|---|---|
| cmegroup.com weekly report | primary_data | ~0.91 | fully_cited |
| bls.gov CPI release | primary_data | ~0.88 | fully_cited |
| silverdata.io inventory snapshot | primary_data | ~0.86 | fully_cited |
| fred.stlouisfed.org data series | primary_data | ~0.92 | fully_cited |

## Tier 2 — peer reviewed

| Source | Class | Score | Action |
|---|---|---|---|
| nature.com article | peer_reviewed | ~0.85 | fully_cited |
| arxiv preprint (no cite/recency hints) | peer_reviewed | ~0.78 | fully_cited |
| arxiv preprint w/ methods + recent date | peer_reviewed | ~0.89 | fully_cited |

## Tier 3 — mainstream press

| Source | Class | Score | Action |
|---|---|---|---|
| reuters.com article citing official data | mainstream_press | ~0.83 | fully_cited |
| reuters.com article w/o excerpt (neutral) | mainstream_press | ~0.74 | fully_cited |
| cnn.com opinion piece (first-person) | mainstream_press | ~0.62 | light_caveat |
| sponsored post on forbes.com | mainstream_press | ~0.55 | light_caveat |

## Tier 4 — industry trade

| Source | Class | Score | Action |
|---|---|---|---|
| kitco.com (treated as industry_trade) | industry_trade | ~0.65 | fully_cited |
| miningweekly.com article | industry_trade | ~0.62 | light_caveat |

## Tier 5 — forums / generic blogs

| Source | Class | Score | Action |
|---|---|---|---|
| reddit.com r/Silverbugs post (no corroboration) | niche_forum | ~0.36 | strong_caveat |
| stackoverflow.com answer (no corroboration) | niche_forum | ~0.40 | strong_caveat |
| medium.com generic post | generic_blog | ~0.34 | strong_caveat |
| substack.com post (no track record) | generic_blog | ~0.38 | strong_caveat |

## Tier 6 — social media

| Source | Class | Score | Action |
|---|---|---|---|
| twitter.com / x.com post | social_media | ~0.27 | strong_caveat |
| linkedin.com post | social_media | ~0.27 | strong_caveat |

## Tier 7 — content farms (auto-detected)

| Source | Class | Score | Action |
|---|---|---|---|
| example.com/best-silver-stocks-2026-top-10 | content_farm | ~0.18 | hidden |
| randomblog.com/reviews/silver-bullion | content_farm | ~0.20 | hidden |
| dealsite.com/affiliate/silver-deal | content_farm | ~0.14 | hidden |

## How corroboration moves the score

A single source scored alone is reduced to ~0.40 corroboration. As independent
sources confirm:

| Corroborators | Corroboration subscore |
|---|---|
| 0 | 0.40 |
| 1 | 0.60 |
| 2 | 0.80 |
| 3+ | 0.85+ |

So the same COMEX weekly report:
- Alone: 0.83 (single-source)
- Confirmed by Reuters: 0.87
- Confirmed by Reuters + Bloomberg: 0.90
- Confirmed by Reuters + Bloomberg + FT: 0.91

This is intentional: a primary_data source shouldn't *need* corroboration, but
the math reflects that more eyes on a claim reduce single-point-of-failure risk.

## Composite scoring across multiple sources (score_claim_tool)

When you call `score_claim_tool` with N supporting sources:

```
composite = 0.60 * best_score + 0.40 * mean(others)
```

So a strong primary source paired with one weak corroborator scores higher
than a weak source paired with three others. This weights toward the strongest
single piece of evidence, which is the right default for "is this claim true".

Verdict tiers:
- `well_supported`: ≥3 independent sources AND best ≥ 0.70
- `supported`: ≥2 independent sources AND best ≥ 0.55, OR ≥1 with best ≥ 0.70
- `contested`: ≥1 independent source but mixed
- `weakly_supported`: only sources below 0.45
- `unsupported`: no sources

## Edge cases the scorer handles

- **Bare domain** ("cmegroup.com" instead of URL): extracts domain, scores on class alone.
- **`.gov` URL** not in tier table: suffix rule catches it, assigns gov_official.
- **URL with /best-/ patterns**: content-farm pattern triggers even on otherwise trusted domain → downgrades to content_farm.
- **Missing excerpt**: citation_provenance defaults to 0.5 (neutral).
- **Missing published date**: recency defaults to 0.5 (neutral).
- **Same domain, multiple articles**: warning fired — not truly independent.
