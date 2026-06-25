# source-credibility-mcp

MCP server that scores research sources on a 0–1 credibility scale with a transparent per-component breakdown.

## Tools

- `cred_health` — server status + current weights/thresholds
- `cred_classify_source(url)` — cheap tier-only classification
- `cred_score_source(url, ...)` — single source, full breakdown
- `cred_score_batch(results)` — score a list of web_search/web_extract results in one call
- `cred_score_claim(claim, supporting_sources, contradicting_sources?)` — composite evidence quality
- `cred_get_breakdown(url, ...)` — why this score?
- `cred_add_custom_domain(domain_pattern, tier, score, note?)` — edit the tier table
- `cred_list_tier_table(tier?)` — inspect current mappings

## Scoring

Each score = weighted sum of:

| Component | Weight | What it measures |
|---|---|---|
| `domain_class` | 0.30 | Tier baseline from `data/domains.json` |
| `citation_provenance` | 0.20 | Count of primary/retrievable citations in the source |
| `corroboration` | 0.15 | Independent sources for the same claim (set externally) |
| `recency` | 0.10 | Age of publish_date |
| `author_transparency` | 0.15 | Named author, byline pattern, expert signal |
| `methodology` | 0.10 | Methodology / sample-size / confidence-interval hints |

Weights and tier mappings are editable in `data/domains.json`. The file is reloaded on every request — no restart needed.

## Tier table

| Tier | Score range | Default behavior |
|---|---|---|
| `primary` | 0.85–1.00 | Shown, fully cited |
| `mainstream` | 0.65–0.85 | Shown, cited |
| `expert` | 0.45–0.65 | Shown with caveat |
| `forum` | 0.25–0.45 | Shown with strong caveat |
| `anonymous` | 0.10–0.25 | Hidden by default, logged |
| `misinfo` | 0.00–0.10 | Hidden, logged |
| `satire` | 0.00–0.10 | Always hidden |

## Inline badge format

`[reuters.com — 0.85 (mainstream)]` — drop directly after the source title in your report.

## Install

```bash
cd C:\Data\Hermes\~\source-credibility-mcp\packages\source-credibility-mcp
"C:\Data\Hermes\hermes-agent\venv\Scripts\python.exe" -m pip install -e .
```

The console script `source-credibility-mcp.exe` lands in `C:\Data\Hermes\hermes-agent\venv\Scripts\`.

Register in `C:\Data\Hermes\config.yaml`:

```yaml
mcp_servers:
  source_credibility:
    command: C:\Data\Hermes\hermes-agent\venv\Scripts\source-credibility-mcp.exe
    enabled: true
```
