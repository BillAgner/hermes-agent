"""Type definitions for the credibility engine.

We deliberately use dataclasses + dict (not Pydantic) so the package has zero
required dependencies beyond the Python stdlib. The MCP layer serializes to
JSON for transport.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SourceClass(str, Enum):
    """Tier classification of a source by its domain baseline.

    The string value is what shows up in JSON output and in any
    inline citations to humans.
    """

    PRIMARY = "primary"        # 0.85-1.00
    MAINSTREAM = "mainstream"  # 0.65-0.85
    EXPERT = "expert"          # 0.45-0.65
    FORUM = "forum"            # 0.25-0.45
    ANONYMOUS = "anonymous"    # 0.10-0.25
    MISINFO = "misinfo"        # 0.00-0.10
    SATIRE = "satire"          # excluded by default
    UNKNOWN = "unknown"        # not classified — scores neutral 0.50 baseline


# Tier display ranges — used for the inline citation badge
# (e.g. "Reuters — 0.85 (mainstream)"). Centralised here so a tweak to
# boundaries only needs to be made once.
TIER_RANGES: dict[SourceClass, tuple[float, float]] = {
    SourceClass.PRIMARY: (0.85, 1.00),
    SourceClass.MAINSTREAM: (0.65, 0.85),
    SourceClass.EXPERT: (0.45, 0.65),
    SourceClass.FORUM: (0.25, 0.45),
    SourceClass.ANONYMOUS: (0.10, 0.25),
    SourceClass.MISINFO: (0.00, 0.10),
    SourceClass.SATIRE: (0.00, 0.10),
    SourceClass.UNKNOWN: (0.00, 1.00),
}


@dataclass
class SourceMeta:
    """Everything we know about a source. Missing fields default to None —
    the scorer treats None signals as 0.5 (neutral) so an unknown author
    isn't punished as hard as a confirmed anonymous one."""

    url: str
    title: str | None = None
    snippet: str | None = None
    author: str | None = None
    publish_date: str | None = None  # ISO 8601 if known
    content: str | None = None       # full or partial body (for methodology heuristics)
    cited_sources: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CredibilityScore:
    """The output of the scorer."""

    score: float                                 # final 0-1
    source_class: SourceClass
    domain_baseline: float                       # raw score from domains.json
    components: dict[str, float]                 # each component's 0-1 value
    weights: dict[str, float]                    # the weights applied
    class_baseline: float                        # the tier baseline (default_score)
    breakdown_explanation: str                   # human-readable breakdown
    should_exclude: bool                         # True if domain is in an exclude-tier
    exclusion_reason: str | None
    domain_match: str | None                     # which pattern matched
    inline_badge: str                            # "[Reuters — 0.85 (mainstream)]"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_class"] = self.source_class.value
        return d
