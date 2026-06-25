"""source-credibility-mcp FastMCP server.

Tools are namespaced with a ``cred_`` prefix to keep them distinct from
generic verbs used by other MCPs. Eight tools:

  cred_health               - server status, current weights, threshold defaults
  cred_score_source         - score a single URL with optional metadata
  cred_classify_source      - just the tier classification (cheap, no scoring)
  cred_score_claim          - score a claim against N supporting sources
  cred_score_batch          - score a list of search results in one call
  cred_get_breakdown        - full transparent breakdown of a score
  cred_add_custom_domain    - add/override a domain in the tier table
  cred_list_tier_table      - inspect current tier mappings

NOTE: Do NOT add ``from __future__ import annotations`` to this file.
Future annotations become strings and break FastMCP's ``Context`` typing
in tool decorators. Annotations below use bare types (X = None, not
``Optional[X]``) for the same reason.
"""

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from credibility_mcp.__about__ import __version__
from credibility_mcp.claims import score_claim
from credibility_mcp.scorer import score_source
from credibility_mcp.tiers import get_table, get_thresholds, get_weights, reload, write_table
from credibility_mcp.types import SourceMeta


mcp = FastMCP("source-credibility")


# ---- helpers ---------------------------------------------------------------


def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _coerce_source(args: dict[str, Any]) -> SourceMeta:
    """Build a SourceMeta from the loose dict shape passed by the agent."""
    return SourceMeta(
        url=args["url"],
        title=args.get("title"),
        snippet=args.get("snippet"),
        author=args.get("author"),
        publish_date=args.get("publish_date"),
        content=args.get("content"),
        cited_sources=args.get("cited_sources") or [],
        extra=args.get("extra") or {},
    )


# ---- health / introspection ------------------------------------------------


@mcp.tool()
def cred_health() -> str:
    """Return server version, current weights, threshold defaults, and
    the loaded tier table summary. Use this to confirm the server is
    wired up and to see the current weighting Bill has in effect."""
    table = get_table()
    tier_summary = {
        name: {
            "default_score": t.get("default_score"),
            "domain_count": len(t.get("domains", [])),
            "exclude": bool(t.get("exclude", False)),
        }
        for name, t in table.get("tiers", {}).items()
    }
    return _to_json({
        "version": __version__,
        "reachable": True,
        "weights": get_weights(),
        "thresholds": get_thresholds(),
        "tiers": tier_summary,
        "table_path": str(Path(__file__).resolve().parent.parent / "data" / "domains.json"),
    })


# ---- scoring ---------------------------------------------------------------


@mcp.tool()
def cred_score_source(
    url: str,
    title: str = None,
    snippet: str = None,
    author: str = None,
    publish_date: str = None,
    content: str = None,
    cited_sources: list[str] = None,
    corroboration_count: int = 0,
) -> str:
    """Score a single source. Returns the final 0-1 score, the
    SourceClass tier, the per-component breakdown, an inline badge
    for citations, and a should_exclude flag.

    Args:
        url: The URL to score (required).
        title: Page title if known.
        snippet: Short text snippet (search-result style).
        author: Author byline if known.
        publish_date: ISO 8601 (YYYY-MM-DD or full ISO) if known.
        content: Full or partial body — improves citation/methodology scoring.
        cited_sources: List of URLs/DOIs the source itself cites.
        corroboration_count: How many other independent sources in the
            same claim support the same conclusion (claim-level context).
    """
    meta = SourceMeta(
        url=url,
        title=title,
        snippet=snippet,
        author=author,
        publish_date=publish_date,
        content=content,
        cited_sources=cited_sources or [],
    )
    result = score_source(meta, corroboration_count=corroboration_count)
    return _to_json(result.to_dict())


@mcp.tool()
def cred_classify_source(url: str) -> str:
    """Return just the tier classification for a URL — cheap (no scoring).
    Useful when you want a fast yes/no on whether a domain is in the
    tier table before bothering to score it."""
    from credibility_mcp.classifier import classify
    src_class, baseline, pattern, note = classify(url)
    return _to_json({
        "url": url,
        "source_class": src_class.value,
        "baseline": baseline,
        "matched_pattern": pattern,
        "note": note,
    })


@mcp.tool()
def cred_score_claim(
    claim: str,
    supporting_sources: list[dict[str, Any]],
    contradicting_sources: list[dict[str, Any]] = None,
    project_min_score: float = None,
) -> str:
    """Score a claim against an evidence chain. Each source is a dict
    matching the cred_score_source args (url, title, snippet, author,
    publish_date, content, cited_sources). Returns the composite
    evidence-quality score, per-support breakdown, weakly_supported
    flag, and a human-readable explanation.

    Args:
        claim: The claim text (used in output only).
        supporting_sources: List of source dicts supporting the claim.
        contradicting_sources: Optional list of source dicts contradicting.
        project_min_score: Override weakly_supported threshold (default 0.65).
    """
    supports = [_coerce_source(s) for s in (supporting_sources or [])]
    contradicts = (
        [_coerce_source(s) for s in contradicting_sources]
        if contradicting_sources
        else []
    )
    result = score_claim(
        claim=claim,
        supporting=supports,
        contradicting=contradicts,
        project_min_score=project_min_score,
    )
    return _to_json(result.to_dict())


@mcp.tool()
def cred_score_batch(results: list[dict[str, Any]]) -> str:
    """Score a batch of web_search / web_extract results in one call.
    Each item should be a dict with at least 'url' and ideally
    'title' / 'snippet' / 'author' / 'publish_date'. Returns a JSON
    array with the score + inline badge for each result, preserving
    order so the agent can attribute scores back to sources.

    This is the hot path for inline scoring — pass the entire
    web_search response here before formatting the report.
    """
    out = []
    for item in results or []:
        url = item.get("url")
        if not url:
            continue
        meta = SourceMeta(
            url=url,
            title=item.get("title"),
            snippet=item.get("snippet") or item.get("description"),
            author=item.get("author"),
            publish_date=item.get("publish_date") or item.get("date"),
            content=item.get("content"),
            cited_sources=item.get("cited_sources") or [],
        )
        scored = score_source(meta, corroboration_count=int(item.get("corroboration_count", 0)))
        out.append({
            "input": item,
            "score": scored.to_dict(),
            "inline_badge": scored.inline_badge,
        })
    return _to_json(out)


@mcp.tool()
def cred_get_breakdown(url: str, publish_date: str = None, author: str = None) -> str:
    """Full transparent breakdown of a score — shows each component
    value, the weight, the matched tier pattern, and the math. Use
    this when the user asks why a source got the score it did."""
    meta = SourceMeta(url=url, publish_date=publish_date, author=author)
    s = score_source(meta)
    return _to_json({
        "url": url,
        "score": s.score,
        "components": s.components,
        "weights": s.weights,
        "domain_baseline": s.domain_baseline,
        "class_baseline": s.class_baseline,
        "source_class": s.source_class.value,
        "matched_pattern": s.domain_match,
        "should_exclude": s.should_exclude,
        "exclusion_reason": s.exclusion_reason,
        "breakdown": s.breakdown_explanation,
        "notes": s.notes,
        "inline_badge": s.inline_badge,
    })


# ---- editable tier table ---------------------------------------------------


@mcp.tool()
def cred_add_custom_domain(
    domain_pattern: str,
    tier: str,
    score: float,
    note: str = None,
) -> str:
    """Add or override a domain's classification in the tier table.
    Edits are persisted to data/domains.json and take effect on the
    next request (no restart needed). Use this when a new source
    appears repeatedly in your research and you want to permanently
    classify it.

    Args:
        domain_pattern: The domain (suffix match). e.g. "myfavoriteblog.com".
        tier: One of: primary, mainstream, expert, forum, anonymous, misinfo, satire.
        score: 0-1 baseline score for this domain.
        note: Optional human-readable note shown in breakdowns.

    Returns:
        Confirmation with the inserted entry and updated tier count.
    """
    if tier not in {"primary", "mainstream", "expert", "forum", "anonymous", "misinfo", "satire"}:
        return _to_json({"error": f"unknown tier: {tier}"})
    if not (0.0 <= score <= 1.0):
        return _to_json({"error": f"score must be 0-1, got {score}"})

    table = get_table()
    tier_entry = table.setdefault("tiers", {}).setdefault(tier, {})
    domains = tier_entry.setdefault("domains", [])
    # Replace if exact pattern already present anywhere
    replaced = False
    for t_name, t in table["tiers"].items():
        for i, entry in enumerate(t.get("domains", [])):
            if entry.get("pattern") == domain_pattern:
                t["domains"][i] = {"pattern": domain_pattern, "score": score}
                if note:
                    t["domains"][i]["note"] = note
                replaced = True
                replaced_tier = t_name
                break
        if replaced:
            break
    if not replaced:
        entry = {"pattern": domain_pattern, "score": score}
        if note:
            entry["note"] = note
        domains.append(entry)
        replaced_tier = tier

    write_table(table)
    return _to_json({
        "ok": True,
        "domain_pattern": domain_pattern,
        "tier": replaced_tier,
        "score": score,
        "replaced_existing": replaced,
        "tier_domain_count": len(domains),
    })


@mcp.tool()
def cred_list_tier_table(tier: str = None) -> str:
    """List domains in a tier (or all tiers). Read-only inspection."""
    table = get_table()
    if tier:
        if tier not in table.get("tiers", {}):
            return _to_json({"error": f"unknown tier: {tier}"})
        return _to_json({tier: table["tiers"][tier]})
    return _to_json(table.get("tiers", {}))


# ---- entry point -----------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
