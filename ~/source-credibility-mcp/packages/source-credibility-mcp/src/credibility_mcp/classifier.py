"""URL → SourceClass.

We classify by domain (eTLD+1) against the tier table. Pattern matching is
suffix-based with exact-match precedence: a domain "x.com" matches the
pattern "x.com" exactly before matching ".com" or any broader suffix.

Special cases handled here:
- DOI URLs (doi.org/10.xxxx/yyyy) — pattern matches as primary, but the
  scorer downgrades slightly because the publisher quality varies.
- Twitter / X canonicalisation — both twitter.com and x.com resolve.
- Substack custom domains — many writers publish at their own domain; we
  fall back to the platform-tier (anonymous) with a note.
"""

from urllib.parse import urlparse

from credibility_mcp.tiers import get_table
from credibility_mcp.types import SourceClass


def _registrable_domain(url: str) -> str:
    """Return a lowercased hostname. Strips common prefixes (www., m.).
    Does not implement the full eTLD+1 algorithm — good enough for the
    classification we do, which is suffix match against patterns like
    '.gov', 'reuters.com', 'subdomain.example.com'."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def classify(url: str) -> tuple[SourceClass, float, str | None, str | None]:
    """Return (SourceClass, baseline_score, matched_pattern, tier_default_note).

    The baseline_score is the per-domain override if present, else the
    tier's default_score.
    """
    host = _registrable_domain(url)
    if not host:
        return SourceClass.UNKNOWN, 0.50, None, "no hostname"

    table = get_table()
    tiers = table.get("tiers", {})

    # Exact domain match first, then suffix match. Track the best (longest)
    # match so 'cmegroup.com' wins over a hypothetical '.com' pattern.
    best: tuple[SourceClass, float, str, str | None] | None = None
    for tier_name, tier in tiers.items():
        try:
            tier_class = SourceClass(tier_name)
        except ValueError:
            continue
        for entry in tier.get("domains", []):
            pattern = entry["pattern"].lower()
            if host == pattern or host.endswith("." + pattern):
                score = float(entry.get("score", tier.get("default_score", 0.5)))
                note = entry.get("note")
                if best is None or len(pattern) > len(best[2]):
                    best = (tier_class, score, pattern, note)

    if best is not None:
        return best

    # Heuristic fallback for hosts not in the table. Cheap and conservative.
    if host.endswith(".gov") or host.endswith(".edu") or host.endswith(".mil"):
        return SourceClass.PRIMARY, 0.88, host, "gov/edu heuristic"

    if ".edu" in host:
        return SourceClass.PRIMARY, 0.80, host, ".edu heuristic"

    # Default: unknown — scorer applies neutral 0.50 baseline
    return SourceClass.UNKNOWN, 0.50, None, "no match in tier table"


def should_exclude_tier(tier_class: SourceClass) -> bool:
    """Return True if this tier is filtered by default (logged but not shown)."""
    table = get_table()
    tier = table.get("tiers", {}).get(tier_class.value, {})
    return bool(tier.get("exclude", False))
