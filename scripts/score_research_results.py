"""Helper module for scoring web_search / web_extract results inline.

Use this from any Hermes session via execute_code:

    from hermes_tools import execute_code  # already in the agent's toolbelt

    # or invoke as a script:
    #   python scripts/score_research_results.py < results.json > report.md

The agent calls the source-credibility MCP's ``cred_score_batch`` tool to
get scores; this module provides the FORMATTING layer so the agent can
produce consistently-styled research output with inline credibility
badges, exclusions, and per-claim composites.

Why split formatting from scoring?
- Scoring lives in the MCP server (process-isolated, restartable).
- Formatting is stateless and small enough to be a sidecar.
- Keeps the MCP tool surface clean — no "format my report" opinion.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


# Inline badge format: "[reuters.com — 0.85 (mainstream)]"
# Color cues (terminal output only — markdown stays plain):
_BADGE_COLOR = {
    "primary":    "\033[1;32m",  # bright green
    "mainstream": "\033[32m",    # green
    "expert":     "\033[36m",    # cyan
    "forum":      "\033[33m",    # yellow
    "anonymous":  "\033[31m",    # red
    "misinfo":    "\033[1;31m",  # bright red
    "satire":     "\033[1;31m",  # bright red
    "unknown":    "\033[90m",    # grey
}
_RESET = "\033[0m"


def _color_for(source_class: str, color: bool) -> str:
    if not color:
        return ""
    return _BADGE_COLOR.get(source_class, _RESET)


def format_inline_badge(badge: str, color: bool = False) -> str:
    """Re-emit the inline badge. Pass color=True for terminal output."""
    if not color:
        return badge
    # Extract source class from "[host — 0.85 (mainstream)]"
    m = re.match(r"\[(\S+)\s+—\s+([\d.]+)\s+\((\w+)\)\]", badge)
    if not m:
        return badge
    host, score, src_class = m.group(1), m.group(2), m.group(3)
    return f"{_color_for(src_class, True)}[{host} — {score} ({src_class})]{_RESET}"


def format_sources_list(
    scored: list[dict[str, Any]],
    project_min_score: float | None = None,
    color: bool = False,
) -> str:
    """Render scored results as a markdown bullet list with inline badges.

    Excluded sources (should_exclude=True) are logged to a separate
    "Filtered out" section so the user can still see what was dropped.

    Args:
        scored: Output of cred_score_batch — list of {input, score, inline_badge}.
        project_min_score: Optional override for flagging weak sources.
        color: If True, ANSI-color the badges (terminal only).
    """
    if project_min_score is None:
        project_min_score = 0.50

    lines: list[str] = []
    shown: list[str] = []
    filtered: list[str] = []

    for entry in scored:
        score_obj = entry.get("score", {})
        input_obj = entry.get("input", {})
        badge = entry.get("inline_badge") or score_obj.get("inline_badge", "")
        url = score_obj.get("domain_match") or input_obj.get("url", "?")
        title = input_obj.get("title") or input_obj.get("snippet", "")[:80]
        s = float(score_obj.get("score", 0.0))
        src_class = score_obj.get("source_class", "unknown")
        should_exclude = bool(score_obj.get("should_exclude"))

        badge_styled = format_inline_badge(badge, color=color)

        if should_exclude:
            filtered.append(
                f"  - {url} — {badge_styled} ({score_obj.get('exclusion_reason', 'filtered')})"
            )
            continue

        flag = ""
        if s < project_min_score:
            flag = "  ⚠ LOW-CREDIBILITY"

        shown.append(f"- [{title}]({input_obj.get('url', '#')}) {badge_styled}{flag}")

    lines.append("## Sources")
    if shown:
        lines.extend(shown)
    else:
        lines.append("_(no sources passed the credibility threshold)_")

    if filtered:
        lines.append("")
        lines.append("## Filtered out (logged, not shown)")
        lines.extend(filtered)

    return "\n".join(lines)


def format_claim(
    claim: str,
    composite: float,
    weakly_supported: bool,
    sources: list[dict[str, Any]],
    color: bool = False,
) -> str:
    """Render a single claim with its composite score + supporting source badges."""
    badge = f"[{composite:.2f} (claim composite)]"
    flag = " ⚠ weakly supported" if weakly_supported else ""
    out = [f"> **{claim}** — {badge}{flag}", ""]
    for src in sources or []:
        sb = src.get("inline_badge", "")
        out.append(f"  - {format_inline_badge(sb, color=color)}")
    return "\n".join(out)


def main() -> None:
    """CLI: read JSON {results: [...], project_min_score?: float} from stdin,
    write markdown to stdout. Lets Bill pipe scored JSON in and get the
    formatted output back."""
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[error] invalid JSON on stdin: {exc}", file=sys.stderr)
        sys.exit(2)
    scored = data.get("scored") or data.get("results") or []
    project_min = data.get("project_min_score")
    print(format_sources_list(scored, project_min_score=project_min))


if __name__ == "__main__":
    main()
