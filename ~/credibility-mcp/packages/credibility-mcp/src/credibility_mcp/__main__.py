"""credibility-mcp: source credibility scoring as MCP tools.

Scores any URL/domain with an explicit 0-1 trust score, a 7-component
breakdown, and inline-rendering helpers. Designed so the agent can show
the score on every citation it makes \u2014 not silently filter.

NOTE: Do not add ``from __future__ import annotations`` to this file.
Future annotations become strings and break FastMCP's ``Context`` typing
in tool decorators. Annotations below use bare types (X = None, not
``Optional[X]``) for the same reason.
"""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from credibility_mcp.__about__ import __version__
from credibility_mcp.log_store import get_artifact, list_artifacts, log_artifact
from credibility_mcp.render import dashboard_panel, inline_markdown
from credibility_mcp.scorer import _load_tier_table, score_claim, score_source


# --- Result helpers --------------------------------------------------------


def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _parse_json_arg(name: str, raw: str) -> Any:
    """Parse a JSON string arg. Empty -> None."""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc


# --- Server ----------------------------------------------------------------


mcp = FastMCP("credibility")


# ---- Health ----------------------------------------------------------------


@mcp.tool()
async def health() -> str:
    """Probe the scorer and report version + tier table load status.

    Returns {ok, version, weights, thresholds, tier_domain_count}.
    Use this to confirm the server is up before long scoring workflows.
    """
    try:
        table = _load_tier_table()
        return _to_json({
            "ok": True,
            "version": __version__,
            "weights": table["weights"],
            "thresholds": table["default_thresholds"],
            "tier_domain_count": len(table["domain_to_class"]),
            "classes": list(table["classes"].keys()),
        })
    except Exception as exc:
        return _to_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


# ---- Scoring ---------------------------------------------------------------


@mcp.tool()
async def score_source_tool(
    url: str,
    title: str = None,
    content_excerpt: str = None,
    author: str = None,
    published: str = None,
    source_class_hint: str = None,
    corroborating_sources: int = 0,
) -> str:
    """Score a single source (URL or bare domain) on a 0-1 trust scale.

    Args:
        url: The source URL (or bare domain).
        title: Optional article/page title (currently unused by the scorer
            but stored on the result for rendering).
        content_excerpt: Optional first ~500 chars of the content; enables
            citation-provenance and methodology heuristics.
        author: Optional author byline.
        published: Optional ISO8601 date ("2026-06-19" or full).
        source_class_hint: Optional override of the auto-detected class.
            One of: primary_data, peer_reviewed, gov_official, primary_doc,
            mainstream_press, industry_trade, recognized_expert_blog,
            niche_forum, generic_blog, social_media, content_farm.
        corroborating_sources: Count of *independent* sources that confirm
            the same claim. 0 = single source; 1 = one other; etc.

    Returns: JSON {score, source_class, threshold_action, components, flags}.
    """
    try:
        result = score_source(
            url=url,
            title=title,
            content_excerpt=content_excerpt,
            author=author,
            published=published,
            source_class_hint=source_class_hint,
            corroborating_sources=corroborating_sources,
        )
        return _to_json(result.to_dict())
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "url": url})


@mcp.tool()
async def score_claim_tool(claim: str, supporting_sources_json: str) -> str:
    """Score a claim from its supporting sources.

    Args:
        claim: The textual claim being supported.
        supporting_sources_json: JSON array of source objects. Each element
            must have at least {"url": "..."}. Other keys (title,
            content_excerpt, author, published, source_class_hint) pass
            through to score_source.

            Example:
            [
              {"url": "https://www.cmegroup.com/...", "title": "..."},
              {"url": "https://www.reuters.com/...", "content_excerpt": "..."},
              {"url": "https://reddit.com/r/Silverbugs/...", "source_class_hint": "niche_forum"}
            ]

    Returns: JSON with composite_score, verdict (well_supported / supported /
    contested / weakly_supported / unsupported), best_source, scored_sources,
    and warnings.
    """
    try:
        sources = _parse_json_arg("supporting_sources_json", supporting_sources_json)
        if not isinstance(sources, list):
            raise ValueError("supporting_sources_json must be a JSON array")
        result = score_claim(claim, sources)
        return _to_json(result)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "claim": claim})


# ---- Rendering -------------------------------------------------------------


@mcp.tool()
async def render_inline_tool(
    scored_sources_json: str,
    show_warnings_only: bool = False,
) -> str:
    """Render scored sources as a markdown bullet list for inline citation.

    Args:
        scored_sources_json: JSON array of scored source objects (output of
            score_source_tool, or {"url": ...} inputs to score_claim_tool).
        show_warnings_only: If true, only render sources flagged as
            strong_caveat or hidden. Use to surface what would have been
            filtered.

    Returns: Markdown text. Each bullet:
        * [domain](url) [c=0.78, mainstream press] — (class=0.88\u00d70.30 ...)

    The "(...)" breakdown shows the named subscore \u00d7 weight so anyone
    reading the report can see exactly which components drove the score.
    """
    try:
        scored = _parse_json_arg("scored_sources_json", scored_sources_json)
        if not isinstance(scored, list):
            raise ValueError("scored_sources_json must be a JSON array")
        return inline_markdown(scored, show_warnings_only=show_warnings_only)
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"


@mcp.tool()
async def render_dashboard_panel_tool(
    scored_sources_json: str,
    research_id: str = None,
    title: str = "Sources used",
) -> str:
    """Render a JSON payload for the dashboard's 'sources used' panel.

    Args:
        scored_sources_json: JSON array of scored source objects.
        research_id: Optional id linking this panel to a logged research artifact.
        title: Panel heading (default "Sources used").

    Returns: JSON with summary stats (totals, average score, breakdown by
    class and by threshold action) and the per-source list sorted by score.
    """
    try:
        scored = _parse_json_arg("scored_sources_json", scored_sources_json)
        if not isinstance(scored, list):
            raise ValueError("scored_sources_json must be a JSON array")
        return _to_json(dashboard_panel(scored, research_id=research_id, title=title))
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})


# ---- Log / persistence -----------------------------------------------------


@mcp.tool()
async def log_research_tool(artifact_json: str) -> str:
    """Persist a research artifact (sources + claims) to the credibility log.

    The dashboard reads the log directory to populate the 'sources used'
    panel for each artifact. Default log dir is
    C:\\Data\\Hermes\\cache\\credibility_log\\ (override with
    CREDIBILITY_LOG_DIR env var).

    Args:
        artifact_json: JSON object with these keys:
          - title (str, required)
          - sources (list[dict], required): scored source objects
          - claims (list[dict], optional): claim text + supporting source urls
          - notes (str, optional)
          - research_id (str, optional): if omitted, auto-generated

    Returns: JSON {path, research_id, bytes}.
    """
    try:
        artifact = _parse_json_arg("artifact_json", artifact_json)
        if not isinstance(artifact, dict):
            raise ValueError("artifact_json must be a JSON object")
        if "title" not in artifact:
            raise ValueError("artifact.title is required")
        if "sources" not in artifact or not isinstance(artifact["sources"], list):
            raise ValueError("artifact.sources must be a list")
        result = log_artifact(artifact)
        return _to_json(result)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def list_artifacts_tool(limit: int = 50) -> str:
    """List logged research artifacts (metadata only).

    Args:
        limit: Max number to return (default 50).

    Returns: JSON array of {research_id, title, logged_at, source_count,
    claim_count, average_score}.
    """
    try:
        return _to_json(list_artifacts(limit=limit))
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_artifact_tool(research_id: str) -> str:
    """Read a full logged research artifact by research_id.

    Args:
        research_id: The id returned by log_research_tool (e.g. "art-1749...").

    Returns: JSON artifact or {"error": "not found", "research_id": ...}.
    """
    try:
        result = get_artifact(research_id)
        if result is None:
            return _to_json({"error": "not found", "research_id": research_id})
        return _to_json(result)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "research_id": research_id})


# --- Entrypoint -------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
