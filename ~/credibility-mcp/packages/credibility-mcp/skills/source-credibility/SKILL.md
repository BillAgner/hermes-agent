---
name: source-credibility
description: "Score any source on a 0-1 trust scale with an explicit, explainable formula — and render the score inline on every citation. Use when the agent is about to cite a URL in a research report, when the user asks 'is this source reliable', when building a literature review, or when the user wants to filter / triage sources by trust level. The scorer is fast, deterministic, and auditable: every subscore is named, every weight is in domain_tiers.json, and there's no hidden LLM in the loop."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, credibility, citations, sources, trust, epistemics]
    related_skills: [arxiv, last30days, open-notebook, commodity-inventory-monitor]
---

# Source Credibility

Score any URL or domain on a 0-1 trust scale with an explicit, auditable formula — and surface the score inline on every citation.

## When to load

- About to cite a source in a research report → `score_source_tool` first, then `render_inline_tool`
- User asks "is X reliable" / "how much should I trust this"
- Building a literature review or news digest and need to triage
- Want to see what the agent filtered out and why
- User wants a "sources used" dashboard view of recent research

## The formula (auditable)

```
credibility = 0.30 * source_class        # primary_data / peer_reviewed / mainstream_press / ...
           + 0.20 * citation_provenance  # does the source cite primary data itself?
           + 0.15 * domain_authority     # known domain vs unknown
           + 0.15 * corroboration        # how many independent sources confirm
           + 0.10 * recency              # freshness vs domain-appropriate window
           + 0.05 * author_track_record  # byline + bio signals
           + 0.05 * methodology          # method/stats/limitations described
```

Every subscore is computed deterministically. Read `src/credibility_mcp/scorer.py` to see exactly how each one works. Weights and thresholds live in `references/domain_tiers.json` — if you want different defaults, edit that file.

## Tier table (the score → class translation)

| Score range | Class examples | Default action |
|---|---|---|
| 0.85-1.00 | primary_data, peer_reviewed, gov_official | fully cited, no caveat |
| 0.65-0.85 | mainstream_press, industry_trade | fully cited |
| 0.45-0.65 | recognized_expert_blog | light caveat |
| 0.25-0.45 | niche_forum, generic_blog | strong caveat |
| 0.00-0.25 | social_media, content_farm | **hidden by default, but logged** |

Defaults in `domain_tiers.json::default_thresholds`. Per-project override goes through the dashboard.

## Stack

- Python 3.11 (matches Hermes venv)
- One dep: `mcp[cli]>=1.0`
- No LLM in the scoring path — everything is a deterministic heuristic. Fast (~ms per source), no per-call cost, no recursive credibility problem.
- Persistence: JSON files under `C:\Data\Hermes\cache\credibility_log\` (override via `CREDIBILITY_LOG_DIR` env var). The dashboard reads this directory for the "sources used" panel.

## Quick workflow

### Score a single source

```
Call: mcp__credibility__score_source_tool
Args:
  url: "https://www.cmegroup.com/markets/silver.html"
  title: "COMEX silver futures"
Returns: {score: 0.91, source_class: "primary_data", threshold_action: "fully_cited", ...}
```

### Score a claim from multiple sources

```
Call: mcp__credibility__score_claim_tool
Args:
  claim: "Registered silver declined for the 8th straight week"
  supporting_sources_json: '[
    {"url": "https://www.cmegroup.com/..."},
    {"url": "https://www.reuters.com/..."},
    {"url": "https://reddit.com/r/Silverbugs/..."}
  ]'
Returns: {composite_score: 0.78, verdict: "supported",
          best_source: {...}, scored_sources: [...], warnings: [...]}
```

### Render citations for a report

After scoring, call `render_inline_tool` to get markdown bullets ready to paste into a research report:

```
Call: mcp__credibility__render_inline_tool
Args:
  scored_sources_json: '[
    {"url": "https://www.cmegroup.com/...", "score": 0.91, ...},
    {"url": "https://reddit.com/...", "score": 0.32, ...}
  ]'
```

Output (each bullet shows the score, class, and which subscore weights drove the score):

```
* [cmegroup.com](https://www.cmegroup.com/...) [c=0.91, primary data] — (class=0.90×0.30 domain=1.00×0.15 cite=0.50×0.20 recy=0.50×0.10)
* [reddit.com](https://reddit.com/...) [c=0.32, niche forum, LOW CREDIBILITY] — (class=0.40×0.30 ...)
```

### Log the artifact (so the dashboard can show it)

```
Call: mcp__credibility__log_research_tool
Args:
  artifact_json: '{
    "title": "Silver COMEX inventory — weekly check",
    "sources": [{...}, {...}],
    "claims": [{"text": "...", "verdict": "supported", "composite_score": 0.78}],
    "notes": "Generated 2026-06-19"
  }'
Returns: {path: "C:\\Data\\Hermes\\cache\\credibility_log\\art-...json", research_id: "art-..."}
```

## What the scorer does NOT do

- Does not call LLMs. No cost, no latency, no recursion. If you want an LLM-based classifier, layer it on top — but the heuristic score is the substrate.
- Does not silently drop sources. The dashboard "sources used" panel shows everything, including filtered ones, so you can pull them back in.
- Does not have a single "credibility" pill. The full component breakdown is always returned so you can disagree with the formula rather than the result.

## Per-project overrides

Default thresholds work for most cases. To override per project:

1. Edit `references/domain_tiers.json` → `default_thresholds` (changes global default)
2. Or pass `thresholds=` to `score_source(...)` directly when calling from Python
3. Or wait for the dashboard panel to expose per-project controls (Phase 2)

## Installation

Already installed via `install_credibility_mcp.ps1`. To re-install:

```
powershell -ExecutionPolicy Bypass -File C:\Data\Hermes\scripts\install_credibility_mcp.ps1
```

To uninstall:

```
powershell -ExecutionPolicy Bypass -File C:\Data\Hermes\scripts\uninstall_credibility_mcp.ps1
```

## References

- `references/domain_tiers.json` — the actual data: weights, classes, domain table, content-farm patterns, recency windows, thresholds
- `references/scoring_examples.md` — worked examples with expected scores for common source patterns
- `src/credibility_mcp/scorer.py` — the formula. Read it to disagree with the weights.
