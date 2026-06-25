"""Claim-level scoring.

A claim's evidence quality isn't just the average of its sources — it's
weighted by independence. Two mainstream news sources citing the same
wire report are weaker than two mainstream sources citing independent
primary data. We approximate independence via domain diversity and a
penalty for repeated use of the same wire pattern.

Output: a ClaimScore with the composite score, the supporting sources,
and a "weakly supported" flag if the composite is below the project
threshold (default 0.65 from default_thresholds).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any

from credibility_mcp.scorer import score_source
from credibility_mcp.tiers import get_table, get_thresholds
from credibility_mcp.types import CredibilityScore, SourceMeta


@dataclass
class ClaimScore:
    """The composite quality of a claim's evidence chain."""

    claim: str
    composite_score: float
    supports: list[CredibilityScore] = field(default_factory=list)
    contradict: list[CredibilityScore] = field(default_factory=list)
    supporting_domains: list[str] = field(default_factory=list)
    independent_count: int = 0
    weakly_supported: bool = False
    rest_on_primary: bool = False
    rest_exclusively_on_low_cred: bool = False
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["supports"] = [s.to_dict() for s in self.supports]
        d["contradict"] = [s.to_dict() for s in self.contradict]
        return d


def _independence_penalty(supports: list[CredibilityScore]) -> float:
    """Return 0.0 (fully independent) → 1.0 (all from same domain).

    Count distinct domains among supports. If most come from a single
    host, penalize heavily.
    """
    if not supports:
        return 0.0
    hosts = [s.domain_match or "unknown" for s in supports]
    counts = Counter(hosts)
    most_common = counts.most_common(1)[0][1]
    return min(1.0, most_common / len(supports))


def score_claim(
    claim: str,
    supporting: list[SourceMeta],
    contradicting: list[SourceMeta] | None = None,
    project_min_score: float | None = None,
) -> ClaimScore:
    """Score a claim's evidence chain.

    Args:
        claim: The claim text (used in output only).
        supporting: SourceMeta list supporting the claim.
        contradicting: Optional SourceMeta list contradicting it.
        project_min_score: Override the default weakly-supported threshold.
    """
    thresholds = get_thresholds()
    weakly_threshold = project_min_score if project_min_score is not None else (
        thresholds.get("weakly_supports_below", 0.65)
    )

    # Score each support. Corroboration count is set externally below
    # because we need the full set first.
    raw_scores = [score_source(m) for m in supporting]

    # Set corroboration based on support count (excluding the source itself).
    n = len(raw_scores)
    scores: list[CredibilityScore] = []
    for i, s in enumerate(raw_scores):
        # Each additional supporting source bumps corroboration up to 0.95
        external = max(0, n - 1)
        boosted_components = dict(s.components)
        if external >= 2:
            boosted_components["corroboration"] = min(1.0, 0.78 + 0.05 * (external - 2))
        elif external == 1:
            boosted_components["corroboration"] = 0.65
        # Recompute the weighted total
        weights = s.weights
        total_weight = sum(weights.get(k, 0.0) for k in boosted_components) or 1.0
        new_total = sum(
            boosted_components[k] * weights.get(k, 0.0)
            for k in boosted_components
        ) / total_weight
        new_total = max(new_total, s.domain_baseline * 0.85)
        new_total = max(0.0, min(1.0, new_total))
        s.score = new_total
        s.components = boosted_components
        s.breakdown_explanation = (
            f"final={new_total:.2f}; "
            + " + ".join(
                f"{k}={v:.2f}×{weights.get(k, 0.0):.2f}"
                for k, v in boosted_components.items()
            )
            + f"; tier_baseline={s.domain_baseline:.2f}; class={s.source_class.value}"
        )
        s.inline_badge = f"[{s.inline_badge.split(' — ')[0][1:]} — {new_total:.2f} ({s.source_class.value})]"
        scores.append(s)

    contradict_scores = (
        [score_source(m) for m in contradicting] if contradicting else []
    )

    if not scores:
        return ClaimScore(
            claim=claim,
            composite_score=0.0,
            weakly_supported=True,
            explanation="No supporting sources provided.",
        )

    # Composite: weighted by source score, with independence penalty.
    indep_penalty = _independence_penalty(scores)
    raw_avg = sum(s.score for s in scores) / len(scores)
    composite = raw_avg * (1.0 - 0.30 * indep_penalty)

    # If a contradiction exists with comparable or higher score, flag.
    if contradict_scores:
        contradict_avg = sum(s.score for s in contradict_scores) / len(contradict_scores)
        if contradict_avg >= composite - 0.10:
            composite *= 0.85  # reduce confidence when contradicted by similar-tier sources

    composite = max(0.0, min(1.0, composite))

    domains = sorted({s.domain_match or "unknown" for s in scores})
    rest_on_primary = all(
        s.source_class.value in {"primary", "mainstream"} for s in scores
    )
    rest_exclusively_on_low = all(s.score < 0.45 for s in scores)
    weakly = composite < weakly_threshold

    explanation_parts = [
        f"composite={composite:.2f} from {len(scores)} sources",
        f"independence_penalty={indep_penalty:.2f}",
        f"domains={domains}",
    ]
    if contradict_scores:
        explanation_parts.append(
            f"contradicted by {len(contradict_scores)} source(s) avg={contradict_avg:.2f}"
        )
    if rest_exclusively_on_low:
        explanation_parts.append("WARN: rests entirely on low-credibility sources")

    return ClaimScore(
        claim=claim,
        composite_score=composite,
        supports=scores,
        contradict=contradict_scores,
        supporting_domains=domains,
        independent_count=len(domains),
        weakly_supported=weakly,
        rest_on_primary=rest_on_primary,
        rest_exclusively_on_low_cred=rest_exclusively_on_low,
        explanation="; ".join(explanation_parts),
    )
