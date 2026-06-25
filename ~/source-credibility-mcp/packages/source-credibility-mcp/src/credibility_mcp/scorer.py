"""SourceMeta → CredibilityScore.

The scorer combines:
  1. Domain baseline (from tier table) — fixed per host.
  2. Component signals — author transparency, citation provenance,
     methodology, recency, corroboration.

The component weights live in domains.json so Bill can tune them without
code changes. The math is intentionally transparent: every component is
recorded with its weight so the breakdown is auditable.

For each component, we use a neutral 0.5 default when the signal is
unknown — an unknown author is not the same as a confirmed anonymous one.
"""

from __future__ import annotations  # noqa: library module, not a FastMCP tool file

import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

from credibility_mcp.classifier import classify, should_exclude_tier
from credibility_mcp.tiers import get_table, get_weights
from credibility_mcp.types import CredibilityScore, SourceClass, SourceMeta


# ---- signal heuristics -----------------------------------------------------


_AUTHOR_PATTERN = re.compile(
    r"\bby\s+([A-Z][a-zA-Z\.\-']{1,40}(?:\s+[A-Z][a-zA-Z\.\-']{1,40})?)"
)
_CITATION_PATTERN = re.compile(
    r"https?://[^\s\)]+|doi:\s*\S+|arxiv:\s*\S+|PMID:\s*\d+", re.IGNORECASE
)
_METHOD_HINTS = re.compile(
    r"\b(method|methodology|we\s+(?:used|analyzed|sampled|polled|collected)|"
    r"data\s+(?:from|source)|sample\s+size|n\s*=|p\s*[<=]|confidence\s+interval|"
    r"margin\s+of\s+error)\b",
    re.IGNORECASE,
)


def _author_transparency(meta: SourceMeta) -> float:
    """0.0 (anonymous, hostile) → 1.0 (named author with track record).

    Heuristics:
    - No author and no obvious 'By X' pattern → 0.65 (likely named on a
      known institutional source — domain class usually implies bylines).
    - 'By X' pattern in content or explicit author field → 0.85.
    - Author field set AND 'staff', 'wire', 'agency' → 0.80 (institutional).
    - Author field with name AND mentions past bylines in snippet → 0.92.
    - Known-anonymous tier (medium, substack unknown author) → 0.30.
    """
    name: str | None = None
    if meta.author and meta.author.strip():
        name = meta.author.strip()
    elif meta.content:
        m = _AUTHOR_PATTERN.search(meta.content[:2000])
        if m:
            name = m.group(1)
    elif meta.snippet:
        m = _AUTHOR_PATTERN.search(meta.snippet[:500])
        if m:
            name = m.group(1)

    if not name:
        return 0.65  # unknown — neutral-positive for institutional sources
    nl = name.lower()
    if nl in {"staff", "wire", "agency", "newsroom", "editorial", "editor"}:
        return 0.80
    if any(token in nl for token in ("reuters", "bloomberg", "associated press", "afp")):
        return 0.88
    if meta.snippet and re.search(
        r"\b(expert|analyst|professor|economist|researcher)\b",
        meta.snippet,
        re.IGNORECASE,
    ):
        return 0.92
    return 0.85


def _citation_provenance(meta: SourceMeta) -> float:
    """0.0 (no citations) → 1.0 (cites multiple primary, retrievable sources).

    Count URLs / DOIs / arxiv IDs / PMIDs in the content; weight by
    density. Above a threshold, bump toward 1.0; below, drop.
    """
    haystacks: list[str] = []
    if meta.cited_sources:
        haystacks.extend(meta.cited_sources)
    if meta.content:
        haystacks.append(meta.content)
    if meta.snippet:
        haystacks.append(meta.snippet)

    if not haystacks:
        return 0.65  # unknown — assume reasonable for institutional sources

    cites: set[str] = set()
    for text in haystacks:
        for m in _CITATION_PATTERN.findall(text):
            cites.add(m.lower())

    # Tier the count. 0 → poor, 1-2 → ok, 3-5 → good, 6+ → strong.
    n = len(cites)
    if n == 0:
        return 0.35
    if n <= 2:
        return 0.65
    if n <= 5:
        return 0.82
    return 0.95


def _methodology(meta: SourceMeta) -> float:
    """0.0 (no methodology disclosed) → 1.0 (full methodology + sample size
    + statistical confidence)."""
    text_parts = [meta.content or "", meta.snippet or ""]
    text = " ".join(text_parts)
    if not text:
        return 0.55  # unknown
    hints = _METHOD_HINTS.findall(text)
    if not hints:
        return 0.45  # article is present but no methodology words
    # Multiple distinct methodology hints = better.
    distinct = len(set(h.lower() for h in hints))
    if distinct >= 3:
        return 0.90
    if distinct == 2:
        return 0.75
    return 0.60


def _recency(meta: SourceMeta, now: datetime | None = None) -> float:
    """For time-sensitive topics. 1.0 if recent; 0.3 if >5y. 0.55 if unknown.

    Domain-agnostic default — there's no way to know if the topic is
    time-sensitive without knowing the topic, so we apply a mild
    recency preference rather than a hard one.
    """
    if not meta.publish_date:
        return 0.55
    try:
        # Accept ISO 8601 or YYYY-MM-DD
        dt = datetime.fromisoformat(meta.publish_date.replace("Z", "+00:00"))
    except ValueError:
        return 0.55
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).days
    if age_days < 0:
        return 0.60  # future-dated — odd but treat as fresh-ish
    if age_days <= 30:
        return 1.00
    if age_days <= 90:
        return 0.92
    if age_days <= 365:
        return 0.80
    if age_days <= 365 * 3:
        return 0.65
    if age_days <= 365 * 5:
        return 0.50
    return 0.30


def _corroboration(meta: SourceMeta, external_count: int = 0) -> float:
    """How many independent sources point to the same claim/URL?

    The scorer accepts this externally because corroboration requires
    cross-source context that lives outside a single SourceMeta. The
    claim-level scorer (claims.py) computes this per claim.

    Default 0.5 (unknown / single source).
    """
    if external_count <= 0:
        return 0.50
    if external_count == 1:
        return 0.65
    if external_count == 2:
        return 0.78
    if external_count <= 4:
        return 0.88
    return 0.95


# ---- scoring ---------------------------------------------------------------


def _format_badge(url: str, score: float, source_class: SourceClass) -> str:
    """Inline citation badge: '[reuters.com — 0.85 (mainstream)]'."""
    host = _registrable_host(url)
    return f"[{host} — {score:.2f} ({source_class.value})]"


def _registrable_host(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host or url


def _breakdown(components: dict[str, float], weights: dict[str, float]) -> str:
    parts = []
    for k, v in components.items():
        w = weights.get(k, 0.0)
        parts.append(f"{k}={v:.2f}×{w:.2f}")
    return " + ".join(parts)


def score_source(
    meta: SourceMeta,
    corroboration_count: int = 0,
) -> CredibilityScore:
    """Score a single source."""
    src_class, baseline, matched_pattern, note = classify(meta.url)
    tier_table = get_table().get("tiers", {}).get(src_class.value, {})
    class_baseline = float(tier_table.get("default_score", 0.5))

    weights = get_weights()
    if not weights:
        # Conservative fallback so we never divide by 0 in the worst case.
        weights = {
            "domain_class": 0.30,
            "citation_provenance": 0.20,
            "corroboration": 0.15,
            "recency": 0.10,
            "author_transparency": 0.15,
            "methodology": 0.10,
        }

    components = {
        "domain_class": baseline,
        "citation_provenance": _citation_provenance(meta),
        "corroboration": _corroboration(meta, corroboration_count),
        "recency": _recency(meta),
        "author_transparency": _author_transparency(meta),
        "methodology": _methodology(meta),
    }

    total_weight = sum(weights.get(k, 0.0) for k in components)
    if total_weight <= 0:
        final = baseline
    else:
        weighted = sum(components[k] * weights.get(k, 0.0) for k in components)
        final = weighted / total_weight

    # Tier mismatch dampener: a baseline of 0.95 (primary) shouldn't drop
    # below 0.80 just because corroboration is unknown. The floor scales
    # with baseline so low-tier sources can still score low on bad signals.
    final = max(final, baseline * 0.85)

    final = max(0.0, min(1.0, final))

    notes: list[str] = []
    if note:
        notes.append(note)
    if matched_pattern:
        notes.append(f"matched pattern: {matched_pattern}")
    if src_class == SourceClass.SATIRE:
        notes.append("SATIRE — always filtered regardless of component signals")
    if src_class == SourceClass.MISINFO:
        notes.append("KNOWN MISINFO — filtered by default")

    exclusion_reason = None
    should_exclude = should_exclude_tier(src_class)
    if should_exclude:
        if src_class == SourceClass.SATIRE:
            exclusion_reason = "Satire tier — would mislead absent context"
        elif src_class == SourceClass.MISINFO:
            exclusion_reason = "Known misinformation tier"
        elif src_class == SourceClass.ANONYMOUS:
            exclusion_reason = "Anonymous tier — logged but not shown by default"

    badge = _format_badge(meta.url, final, src_class)
    explanation = (
        f"final={final:.2f}; "
        + _breakdown(components, weights)
        + f"; tier_baseline={baseline:.2f}; class={src_class.value}"
    )

    return CredibilityScore(
        score=final,
        source_class=src_class,
        domain_baseline=baseline,
        components=components,
        weights=weights,
        class_baseline=class_baseline,
        breakdown_explanation=explanation,
        should_exclude=should_exclude,
        exclusion_reason=exclusion_reason,
        domain_match=matched_pattern,
        inline_badge=badge,
        notes=notes,
    )
