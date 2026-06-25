"""Pydantic schema for research-project primitive.

A research project is a persistent, structured epistemic object. It is the
canonical substrate for multi-session research — hypothesis tracking,
evidence accumulation, contradictions, and dead ends. Each project lives
in its own JSON file on disk and is mirrored as a notebook in the
open-notebook service so source URLs can be browsed in the open-notebook UI.

The data model was designed in collaboration with Bill Agner (2026-06-20).
See ``skills/research-project/SKILL.md`` for the user-facing guide.

Key invariants:
    - ``id`` fields are slug-safe (lowercase, hyphens, no spaces) and stable
      for the life of the project.
    - Confidence values are floats in [0.0, 1.0].
    - Evidence weight is a float in [0.0, 1.0]; 0.0 = untrusted rumor,
      1.0 = peer-reviewed primary source.
    - Source URLs are mirrored as open-notebook sources so the user can
      click through to the original.
    - Manual overrides (user-edited fields) are logged in the timeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Atomic sub-objects
# ---------------------------------------------------------------------------


class Hypothesis(BaseModel):
    """A claim with a confidence score. Confidence evolves as evidence accrues."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier, e.g. 'H1', 'H2'.")
    claim: str = Field(description="The hypothesis statement.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in [0.0, 1.0]."
    )
    last_updated: str = Field(
        default_factory=_utcnow_iso, description="ISO-8601 UTC."
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Why this confidence value? What evidence supports it?",
    )


class Question(BaseModel):
    """An open or answered sub-question the project is investigating."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier, e.g. 'Q1', 'Q2'.")
    text: str = Field(description="The question text.")
    status: Literal["open", "answered", "dead-end"] = "open"
    answer: Optional[str] = Field(
        default=None, description="Filled when status == 'answered'."
    )
    opened: str = Field(default_factory=_utcnow_iso)
    answered: Optional[str] = Field(default=None)


class Evidence(BaseModel):
    """A claim with source provenance. The atomic unit of evidence.

    Source URLs are also mirrored as open-notebook sources by the MCP
    server so the user can browse them in the open-notebook UI.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable identifier, e.g. 'E1', 'E2'.")
    claim: str = Field(description="The evidence statement.")
    sources: list[str] = Field(
        default_factory=list,
        description="URLs (or short citations) backing the claim.",
    )
    source_types: list[str] = Field(
        default_factory=list,
        description="Optional source-type tags, e.g. ['primary', 'cme-bulletin'].",
    )
    weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Credibility weight in [0.0, 1.0]. "
        "1.0 = peer-reviewed primary source, 0.0 = untrusted rumor.",
    )
    added: str = Field(default_factory=_utcnow_iso)
    note: Optional[str] = Field(default=None, description="Optional context.")


class Contradiction(BaseModel):
    """Two evidence items (or hypotheses) that disagree. Explicit, not buried."""

    model_config = ConfigDict(extra="forbid")

    id: str
    claim_a_id: str = Field(description="ID of the first conflicting claim/hypothesis.")
    claim_b_id: str = Field(description="ID of the second conflicting claim/hypothesis.")
    interpretation: str = Field(
        description="How the contradiction was resolved or why it stands."
    )
    added: str = Field(default_factory=_utcnow_iso)


class DeadEnd(BaseModel):
    """A path that was investigated and abandoned. Saves future re-work."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = Field(
        description="What was tried and why it didn't pan out."
    )
    added: str = Field(default_factory=_utcnow_iso)


class TimelineEvent(BaseModel):
    """A chronological log of 'what we learned when'."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(default_factory=_utcnow_iso)
    event: str = Field(description="One-line description of the event.")
    kind: Literal["auto", "manual"] = "auto"


# ---------------------------------------------------------------------------
# Top-level project
# ---------------------------------------------------------------------------


StatusLiteral = Literal["active", "paused", "concluded", "archived"]


class ResearchProject(BaseModel):
    """The root object. One per project.

    Stored as JSON at ``<storage_root>/<slug>/state.json`` and mirrored
    to open-notebook as a notebook with the same name.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description="Stable slug identifier (e.g. 'silver-comex-inventory'). "
        "Must be lowercase, hyphens, no spaces."
    )
    title: str = Field(description="Human-readable project title.")
    scope: str = Field(description="The question or scope the project addresses.")
    status: StatusLiteral = "active"

    # Bookkeeping
    created: str = Field(default_factory=_utcnow_iso)
    last_active: str = Field(default_factory=_utcnow_iso)
    last_session: Optional[str] = Field(
        default=None, description="Hermes session ID that last touched this project."
    )

    # open-notebook mirror
    notebook_id: Optional[str] = Field(
        default=None,
        description="open-notebook notebook id (e.g. 'notebook:abc123'). "
        "Set when the project is first created.",
    )

    # Structured epistemic state
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    dead_ends: list[DeadEnd] = Field(default_factory=list)

    timeline: list[TimelineEvent] = Field(default_factory=list)
    related_projects: list[str] = Field(
        default_factory=list,
        description="Slugs of related projects for cross-referencing.",
    )
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not v or not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"project id must be slug-safe (lowercase, hyphens, "
                f"underscores, alphanumerics); got {v!r}"
            )
        if " " in v:
            raise ValueError(f"project id must not contain spaces; got {v!r}")
        return v.lower()

    # ----- helpers -----

    def touch(self, event: str, session_id: Optional[str] = None) -> None:
        """Update last_active and append a timeline event."""
        self.last_active = _utcnow_iso()
        if session_id:
            self.last_session = session_id
        self.timeline.append(TimelineEvent(event=event))

    def confidence_overall(self) -> Optional[float]:
        """Mean of hypothesis confidences, or None if no hypotheses."""
        if not self.hypotheses:
            return None
        return round(
            sum(h.confidence for h in self.hypotheses) / len(self.hypotheses), 3
        )


__all__ = [
    "Hypothesis",
    "Question",
    "Evidence",
    "Contradiction",
    "DeadEnd",
    "TimelineEvent",
    "ResearchProject",
]