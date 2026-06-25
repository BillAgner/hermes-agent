"""Source credibility scorer.

Computes a 0-1 credibility score for any URL/domain from explicit subscores.
No LLM calls — every component is a deterministic heuristic so the formula is
auditable and explainable. If you disagree with a score, you can read this
file and see exactly why.

Formula (weights in domain_tiers.json):

    credibility = 0.30 * source_class
                + 0.20 * citation_provenance
                + 0.15 * domain_authority
                + 0.15 * corroboration
                + 0.10 * recency
                + 0.05 * author_track_record
                + 0.05 * methodology

Every input is optional. Missing inputs produce neutral (0.5) defaults so
partial evidence still produces a usable score rather than a crash.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Domain table loading
# ---------------------------------------------------------------------------

_DEFAULT_TIER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "references"
    / "domain_tiers.json"
)


def _load_tier_table(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_TIER_PATH
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Subscore dataclass — fully transparent, no hidden state
# ---------------------------------------------------------------------------


@dataclass
class CredibilityScore:
    """Result of scoring a single source. All fields are public."""

    url: str
    domain: str
    source_class: str
    score: float  # final 0-1
    components: dict[str, float]  # subscore name -> 0-1
    weights: dict[str, float]  # weights used
    threshold_action: str  # one of "hidden", "strong_caveat", "light_caveat", "fully_cited"
    flags: list[str] = field(default_factory=list)  # human-readable reasons
    source_class_baseline: float = 0.0
    scored_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_domain(url_or_domain: str) -> str:
    """Extract and lowercase the registrable domain from a URL or bare domain.

    Strips www., lowercases, and returns host. Handles bare domains too.
    """
    s = (url_or_domain or "").strip()
    if not s:
        return ""
    if "://" not in s:
        # Bare domain — synthesize a URL
        s = "http://" + s
    try:
        host = urlparse(s).hostname or ""
    except Exception:
        return ""
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _suffix_class(domain: str, suffix_rules: dict[str, str]) -> str | None:
    for suffix, cls in suffix_rules.items():
        if domain.endswith(suffix):
            return cls
    return None


def _detect_content_farm(
    url: str, signals: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Returns (is_farm, reasons)."""
    reasons: list[str] = []
    url_l = (url or "").lower()
    for pat in signals.get("url_patterns", []):
        if pat in url_l:
            reasons.append(f"URL pattern: '{pat}'")
    domain = _normalize_domain(url)
    for sub in signals.get("domain_substrings", []):
        if sub in domain:
            reasons.append(f"Domain substring: '{sub}'")
    return (bool(reasons), reasons)


def _recency_score(
    published: str | None,
    source_class: str,
    recency_windows: dict[str, Any],
    now: datetime | None = None,
) -> tuple[float, list[str]]:
    """0-1 freshness score. Within fresh_days -> 1.0. Past stale_days -> 0.0.

    Linear decay between the two. Missing published date -> neutral 0.5.
    """
    if not published:
        return (0.5, ["no published date; recency assumed neutral"])
    win = recency_windows.get(source_class, recency_windows["default"])
    fresh = win["fresh_days"]
    stale = win["stale_days"]

    parsed: datetime | None = None
    try:
        # Accept ISO8601 with or without Z
        s = published.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return (0.5, [f"could not parse published date '{published}'"])

    n = now or datetime.now(timezone.utc)
    age_days = (n - parsed).days
    if age_days <= fresh:
        return (1.0, [f"fresh: {age_days}d (window {fresh}d)"])
    if age_days >= stale:
        return (0.0, [f"stale: {age_days}d (window {stale}d)"])
    # Linear decay between fresh and stale
    frac = (age_days - fresh) / max(stale - fresh, 1)
    score = round(1.0 - frac, 3)
    return (score, [f"age {age_days}d (fresh={fresh}d, stale={stale}d)"])


def _citation_provenance_score(
    content_excerpt: str | None, url: str
) -> tuple[float, list[str]]:
    """Heuristic for whether the source cites primary data itself.

    For news/analytical pieces, citing primary sources bumps credibility.
    For primary data itself, the score is maxed (it IS the primary source).
    """
    if not content_excerpt:
        return (0.5, ["no content excerpt provided; assuming neutral"])
    text = content_excerpt.lower()
    signals = []
    score = 0.5

    # Positive signals
    if re.search(r"\baccording to\b", text):
        score += 0.10
        signals.append("'according to' phrasing")
    if re.search(r"\bdata (from|show[s]?|indicates?)\b", text):
        score += 0.08
        signals.append("references data")
    if re.search(r"\b(published|reported|cited)\b.{0,30}\b(study|report|index)\b", text):
        score += 0.08
        signals.append("cites study/report")
    if re.search(r"\b\([^)]*\d{4}\)\b", text):
        score += 0.05
        signals.append("academic-style year citation")
    if re.search(r"https?://", text):
        # The excerpt contains at least one link (could be to a source)
        score += 0.05
        signals.append("contains link(s)")
    if re.search(r"\b(source:|source:|fig\.?\s*\d|table\s*\d)\b", text):
        score += 0.05
        signals.append("labeled source/figure")

    # Negative signals
    if re.search(r"\b(in my opinion|i believe|i think|i feel)\b", text):
        score -= 0.15
        signals.append("first-person opinion")
    if re.search(r"\b(disclaimer|nothing (here|on this (site|page)) (is|constitutes) (financial|investment) advice)\b", text):
        score -= 0.05
        signals.append("financial-advice disclaimer (slight)")
    if re.search(r"\b(sponsored|paid promotion|affiliate)\b", text):
        score -= 0.15
        signals.append("sponsored/affiliate disclosure")

    return (max(0.0, min(1.0, score)), signals)


def _author_methodology_score(
    author: str | None,
    content_excerpt: str | None,
    published: str | None,
) -> tuple[float, float, list[str]]:
    """Returns (author_track_record_score, methodology_score, signals)."""
    signals: list[str] = []
    a_score = 0.5
    m_score = 0.5

    if author:
        a_score += 0.15
        signals.append(f"author byline: {author}")
        # Bonus for multi-word or institutional author
        if len(author.split()) >= 2:
            a_score += 0.10
            signals.append("multi-word author (institutional or full name)")
    else:
        signals.append("no author byline")

    if content_excerpt:
        text = content_excerpt.lower()
        if re.search(r"\b(method|methodology|approach|model|equation)\b", text):
            m_score += 0.15
            signals.append("methodology described")
        if re.search(r"\b(sample size|n\s*=\s*\d|coefficient|p\s*<|confidence interval)\b", text):
            m_score += 0.20
            signals.append("quantitative/statistical signals")
        if re.search(r"\b(limitations|caveats|confidence interval|error bar)\b", text):
            m_score += 0.10
            signals.append("limitations/uncertainty discussed")
        if re.search(r"\b(results|findings|we find|we show)\b", text):
            m_score += 0.10
            signals.append("explicit results language")

    if published:
        m_score += 0.05
        signals.append("publication date present")

    a_score = max(0.0, min(1.0, a_score))
    m_score = max(0.0, min(1.0, m_score))
    return (a_score, m_score, signals)


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


def score_source(
    url: str,
    *,
    title: str | None = None,
    content_excerpt: str | None = None,
    author: str | None = None,
    published: str | None = None,
    source_class_hint: str | None = None,
    corroborating_sources: int = 0,
    tier_table: dict[str, Any] | None = None,
    thresholds: dict[str, float] | None = None,
) -> CredibilityScore:
    """Score a single source. See module docstring for the formula.

    Args:
        url: The source URL (or bare domain).
        title: Optional article/page title.
        content_excerpt: Optional excerpt for citation-provenance heuristics.
        author: Optional author byline.
        published: Optional ISO8601 published date.
        source_class_hint: Optional caller-provided override (e.g. "peer_reviewed").
        corroborating_sources: Count of *independent* sources that confirm the
            same claim. Caller computes this from score_claim, then can re-score
            individual sources via this param.
        tier_table: Loaded from disk by default; pass a custom dict to override.
        thresholds: Visibility thresholds; defaults from tier_table.
    """
    table = tier_table or _load_tier_table()
    weights = dict(table["weights"])
    classes = table["classes"]
    domain_map = table["domain_to_class"]
    suffix_rules = table["suffix_rules"]
    farm_signals = table["content_farm_signals"]
    recency_windows = table["recency_windows"]
    thr = dict(thresholds or table["default_thresholds"])

    flags: list[str] = []
    domain = _normalize_domain(url)

    # 1. Source class
    if source_class_hint and source_class_hint in classes:
        source_class = source_class_hint
        flags.append(f"caller-hinted class: {source_class}")
    elif domain in domain_map:
        source_class = domain_map[domain]
    else:
        suffix_cls = _suffix_class(domain, suffix_rules)
        if suffix_cls:
            source_class = suffix_cls
        else:
            # Heuristic: check for content farm signals first
            is_farm, farm_reasons = _detect_content_farm(url, farm_signals)
            if is_farm:
                source_class = "content_farm"
                flags.extend(farm_reasons)
            else:
                source_class = "unknown"

    # 2. Apply content-farm penalty if class was set higher
    if source_class != "content_farm":
        is_farm, farm_reasons = _detect_content_farm(url, farm_signals)
        if is_farm:
            flags.append(
                f"content-farm signals downgrade from {source_class}: " + "; ".join(farm_reasons)
            )
            source_class = "content_farm"

    baseline = classes[source_class]["score_baseline"]
    source_class_subscore = baseline

    # 3. Domain authority — bonus if domain is in our table explicitly
    if domain in domain_map or domain.endswith(tuple(suffix_rules.keys())):
        domain_authority_subscore = 1.0
        flags.append(f"recognized domain: {domain}")
    elif source_class in ("primary_data", "peer_reviewed", "gov_official", "primary_doc"):
        domain_authority_subscore = 0.85
    elif source_class == "mainstream_press":
        domain_authority_subscore = 0.85
    elif source_class == "industry_trade":
        domain_authority_subscore = 0.70
    else:
        domain_authority_subscore = 0.45
        flags.append("domain not in known table")

    # 4. Citation provenance
    cite_score, cite_signals = _citation_provenance_score(content_excerpt, url)
    flags.extend(f"cite: {s}" for s in cite_signals)

    # 5. Recency
    rec, rec_signals = _recency_score(published, source_class, recency_windows)
    flags.extend(f"recency: {s}" for s in rec_signals)

    # 6. Author + methodology
    a_score, m_score, am_signals = _author_methodology_score(
        author, content_excerpt, published
    )
    flags.extend(f"author/method: {s}" for s in am_signals)

    # 7. Corroboration — convert count to 0-1
    # 0 corroborators -> 0.4 (neutral-low, single-source is risky)
    # 1 -> 0.6, 2 -> 0.8, 3+ -> 1.0
    if corroborating_sources <= 0:
        corroboration_subscore = 0.40
        flags.append("no corroborating independent sources")
    elif corroborating_sources == 1:
        corroboration_subscore = 0.60
    elif corroborating_sources == 2:
        corroboration_subscore = 0.80
    else:
        corroboration_subscore = min(1.0, 0.85 + 0.05 * (corroborating_sources - 3))
    flags.append(f"corroboration: {corroborating_sources} independent source(s)")

    # Final weighted score
    components = {
        "source_class": source_class_subscore,
        "citation_provenance": cite_score,
        "domain_authority": domain_authority_subscore,
        "corroboration": corroboration_subscore,
        "recency": rec,
        "author_track_record": a_score,
        "methodology": m_score,
    }

    final = sum(components[k] * weights[k] for k in weights)
    final = round(max(0.0, min(1.0, final)), 3)

    # Threshold action
    if final < thr["hidden_below"]:
        action = "hidden"
    elif final < thr["strong_caveat_below"]:
        action = "strong_caveat"
    elif final < thr["light_caveat_below"]:
        action = "light_caveat"
    else:
        action = "fully_cited"

    return CredibilityScore(
        url=url,
        domain=domain,
        source_class=source_class,
        score=final,
        components=components,
        weights=weights,
        threshold_action=action,
        flags=flags,
        source_class_baseline=baseline,
        scored_at=datetime.now(timezone.utc).isoformat(),
    )


def score_claim(
    claim: str,
    supporting_sources: list[dict[str, Any]],
    *,
    tier_table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a claim from its supporting sources.

    Args:
        claim: The textual claim being supported.
        supporting_sources: List of dicts each containing at least
            {"url": ..., "score": float (optional), ...}.
            Other keys pass through to score_source.

    Returns:
        {
          "claim": str,
          "composite_score": float (0-1),
          "verdict": "well_supported" | "supported" | "contested" | "weakly_supported" | "unsupported",
          "source_count": int,
          "best_source": {...},
          "scored_sources": [...],
          "warnings": [...]
        }
    """
    if not supporting_sources:
        return {
            "claim": claim,
            "composite_score": 0.0,
            "verdict": "unsupported",
            "source_count": 0,
            "best_source": None,
            "scored_sources": [],
            "warnings": ["no supporting sources provided"],
        }

    scored: list[CredibilityScore] = []
    for src in supporting_sources:
        kwargs = {k: v for k, v in src.items() if k != "url"}
        # If caller already computed a score, use it for corroboration math;
        # otherwise compute it.
        scored.append(score_source(src["url"], **kwargs))

    # Independent corroboration count = number of *distinct* sources whose score >= 0.45
    independent = [s for s in scored if s.score >= 0.45]
    n_indep = len(independent)

    # Re-score each source with its corroboration count for a final per-source score
    rescored: list[CredibilityScore] = []
    for s in scored:
        kwargs = {
            "title": None,
            "content_excerpt": None,
            "author": None,
            "published": None,
            "source_class_hint": s.source_class,
            "corroborating_sources": max(0, n_indep - 1),
            "tier_table": tier_table,
        }
        rescored.append(
            score_source(s.url, **kwargs)
        )

    # Composite: weighted toward the best, with corroboration boost
    if not rescored:
        composite = 0.0
        verdict = "unsupported"
    else:
        best = max(rescored, key=lambda x: x.score)
        # Composite is 60% best + 40% average of others
        others = [s.score for s in rescored if s is not best]
        if others:
            avg_others = sum(others) / len(others)
        else:
            avg_others = best.score
        composite = round(0.6 * best.score + 0.4 * avg_others, 3)

        if n_indep >= 3 and best.score >= 0.70:
            verdict = "well_supported"
        elif n_indep >= 2 and best.score >= 0.55:
            verdict = "supported"
        elif n_indep >= 1 and best.score >= 0.70:
            verdict = "supported"
        elif n_indep >= 1:
            verdict = "contested"
        else:
            verdict = "weakly_supported"

    warnings: list[str] = []
    if n_indep == 0:
        warnings.append("no source above 0.45 threshold")
    if len({s.domain for s in rescored}) == 1 and len(rescored) > 1:
        warnings.append(
            f"all {len(rescored)} sources share the same domain "
            f"({rescored[0].domain}) — not truly independent"
        )

    return {
        "claim": claim,
        "composite_score": composite,
        "verdict": verdict,
        "source_count": len(rescored),
        "independent_count": n_indep,
        "best_source": rescored[max(range(len(rescored)), key=lambda i: rescored[i].score)].to_dict() if rescored else None,
        "scored_sources": [s.to_dict() for s in rescored],
        "warnings": warnings,
    }
