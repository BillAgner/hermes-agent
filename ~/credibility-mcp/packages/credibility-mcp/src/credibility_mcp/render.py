"""Rendering helpers — turn scored sources into human-readable text.

Two outputs:
  * `inline_markdown` — markdown snippet to embed in research reports,
    with `[name \u2014 score]` after each citation.
  * `dashboard_panel` — JSON for the dashboard's "sources used" panel.
"""

from __future__ import annotations

from typing import Any

from credibility_mcp.scorer import CredibilityScore


def _short_name(source: dict[str, Any] | CredibilityScore) -> str:
    """Pull a short, readable label out of a scored source.

    Tries (in order): title, URL last path segment, domain.
    """
    if isinstance(source, CredibilityScore):
        return source.domain or source.url
    d = source.get("domain") or source.get("url", "")
    return d


def inline_markdown(
    scored: list[CredibilityScore] | list[dict[str, Any]],
    *,
    show_score: bool = True,
    show_class: bool = True,
    show_warnings_only: bool = False,
) -> str:
    """Render a list of scored sources as a markdown bullet list.

    Each bullet: `* [Domain](url) [c=0.78, mainstream_press]` style.
    Use this in research reports to make every citation's trust level visible.
    """
    lines: list[str] = []
    for src in scored:
        if isinstance(src, CredibilityScore):
            d = src.to_dict()
        else:
            d = src
        url = d.get("url", "")
        domain = d.get("domain", "")
        score = d.get("score", 0.0)
        cls = d.get("source_class", "unknown")
        action = d.get("threshold_action", "fully_cited")
        comp = d.get("components", {})
        w = d.get("weights", {})

        if show_warnings_only and action not in ("strong_caveat", "hidden"):
            continue

        tag_bits: list[str] = []
        if show_score:
            tag_bits.append(f"c={score:.2f}")
        if show_class:
            tag_bits.append(cls.replace("_", " "))
        if action == "strong_caveat":
            tag_bits.append("LOW CREDIBILITY")
        elif action == "hidden":
            tag_bits.append("FILTERED")
        tag = f" [{', '.join(tag_bits)}]" if tag_bits else ""

        # Component breakdown (only the named ones, not recency/cite)
        breakdown_bits: list[str] = []
        for name, weight_key in [
            ("class", "source_class"),
            ("domain", "domain_authority"),
            ("cite", "citation_provenance"),
            ("recy", "recency"),
        ]:
            v = comp.get(weight_key)
            if v is not None:
                wgt = w.get(weight_key, 0)
                breakdown_bits.append(f"{name}={v:.2f}\u00d7{wgt:.2f}")
        breakdown = f" \u2014 ({' '.join(breakdown_bits)})" if breakdown_bits else ""

        lines.append(f"* [{domain or url}]({url}){tag}{breakdown}")

    return "\n".join(lines)


def dashboard_panel(
    scored: list[CredibilityScore] | list[dict[str, Any]],
    *,
    research_id: str | None = None,
    title: str = "Sources used",
) -> dict[str, Any]:
    """Render a JSON payload for the dashboard 'sources used' panel.

    Shape:
      {
        "title": str,
        "research_id": str | None,
        "summary": { "total": N, "filtered": N, "low_credibility": N, "...": ... },
        "sources": [ { url, domain, source_class, score, action, ... }, ... ]
      }
    """
    normalized: list[dict[str, Any]] = []
    for s in scored:
        if isinstance(s, CredibilityScore):
            normalized.append(s.to_dict())
        else:
            normalized.append(s)

    total = len(normalized)
    by_class: dict[str, int] = {}
    by_action: dict[str, int] = {}
    weighted_sum = 0.0

    for s in normalized:
        cls = s.get("source_class", "unknown")
        action = s.get("threshold_action", "fully_cited")
        by_class[cls] = by_class.get(cls, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1
        weighted_sum += s.get("score", 0.0)

    avg_score = round(weighted_sum / total, 3) if total else 0.0

    return {
        "title": title,
        "research_id": research_id,
        "summary": {
            "total": total,
            "average_score": avg_score,
            "by_class": by_class,
            "by_action": by_action,
            "filtered_count": by_action.get("hidden", 0),
            "low_credibility_count": by_action.get("strong_caveat", 0),
        },
        "sources": sorted(normalized, key=lambda x: x.get("score", 0), reverse=True),
    }
