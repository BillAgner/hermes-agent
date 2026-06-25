"""research-project-mcp: MCP server for persistent structured research state.

This server exposes 17 tools (``rp_*``) for managing long-lived epistemic
state across sessions — hypothesis tracking, evidence accumulation,
contradictions, dead-ends, and reports. Each project is the canonical
owner of its state and persists as a JSON file under
``C:\\Data\\Hermes\\research_projects\\<slug>\\state.json``.

open-notebook (http://localhost:5055/api) is used *only* as a browseable
mirror: each project gets a matching notebook, and each evidence URL is
mirrored as a ``create_source_link`` so Bill can click through to the
original source. If open-notebook is unreachable, the canonical JSON state
is still written — the failure is surfaced as a ``warning`` field in the
response, never raised.

NOTE: Do not add ``from __future__ import annotations`` to this file.
Future annotations become strings and break FastMCP's tool-decorator
typing (``inspect.signature`` chokes on string forward references).
Annotations below use bare types (``X = None``, not ``Optional[X]``).
"""

import asyncio
import json
import os
import random
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from . import __version__
from .schema import (
    Contradiction,
    DeadEnd,
    Evidence,
    Hypothesis,
    Question,
    ResearchProject,
    TimelineEvent,
    _utcnow_iso,
)
from .storage import (
    DEFAULT_STORAGE_ROOT,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    archive_project,
    create_project as _create_project_on_disk,
    delete_project as _delete_project_on_disk,
    list_projects as _list_projects_on_disk,
    list_syntheses as _list_syntheses_on_disk,
    list_all_syntheses as _list_all_syntheses_on_disk,
    load_project,
    load_synthesis as _load_synthesis_on_disk,
    next_id,
    save_project,
    save_synthesis as _save_synthesis_on_disk,
)


# --- Configuration -----------------------------------------------------------

DEFAULT_OPEN_NOTEBOOK_URL = "http://localhost:5055"
DEFAULT_TIMEOUT_S = 10.0
HEALTH_TIMEOUT_S = 3.0

# Transient-failure retry for open-notebook mirror calls. Retries only on
# connection-level / timeout errors (not on HTTP 4xx/5xx responses, which are
# real failures like 404 or CAPTCHA-blocked pages). Backoff is exponential
# starting at _BASE_BACKOFF_S with ±_JITTER_S random jitter; total attempts =
# 1 initial + _MAX_RETRIES retries.
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)
_MAX_RETRIES = 2  # so total attempts = 1 + 2 = 3
_BASE_BACKOFF_S = 0.5
_JITTER_S = 0.1


def _open_notebook_base() -> str:
    """Resolve the open-notebook base URL, appending /api if needed."""
    raw = (os.environ.get("OPEN_NOTEBOOK_URL") or DEFAULT_OPEN_NOTEBOOK_URL).rstrip("/")
    if not raw.endswith("/api"):
        raw = raw + "/api"
    return raw


def _open_notebook_headers() -> dict[str, str]:
    token = os.environ.get("OPEN_NOTEBOOK_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _storage_root() -> Path:
    env = os.environ.get("RESEARCH_PROJECTS_DIR")
    if env:
        return Path(env)
    return DEFAULT_STORAGE_ROOT


# --- open-notebook mirror client --------------------------------------------


class MirrorError(RuntimeError):
    """Raised when the open-notebook mirror call fails."""

    def __init__(self, status: int, detail: str, op: str):
        super().__init__(f"{op}: open-notebook {status}: {detail}")
        self.status = status
        self.detail = detail
        self.op = op


class OpenNotebookMirror:
    """Thin async httpx wrapper used *only as a mirror* — never authoritative.

    Every call here is best-effort. The MCP layer catches ``MirrorError``
    and any other ``Exception`` and reports it as a warning, never raises
    to the caller.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ):
        self.base_url = base_url or _open_notebook_base()
        self.timeout_s = timeout_s if timeout_s is not None else DEFAULT_TIMEOUT_S
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            headers={"Accept": "application/json", **_open_notebook_headers()},
        )

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        # Retry transient (connection / timeout) errors only. Do not retry
        # HTTP 4xx/5xx responses — those are real failures.
        last_exc: httpx.HTTPError | None = None
        resp = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.request(
                    method, path, params=clean_params, json=json_body
                )
                break  # success — exit retry loop
            except _RETRYABLE_EXC as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BASE_BACKOFF_S * (2 ** attempt) + random.uniform(
                        -_JITTER_S, _JITTER_S
                    )
                    await asyncio.sleep(delay)
                    continue
                # Final attempt failed — surface as MirrorError(0, ...).
                raise MirrorError(0, str(exc), path) from exc
        if resp is None:
            # Defensive: should be unreachable — the loop either breaks on
            # success or raises on final failure. If we get here, treat as
            # a transient failure using the last recorded exception.
            raise MirrorError(
                0, str(last_exc) if last_exc else "unknown transient error", path
            ) from last_exc

        if resp.status_code >= 400:
            detail = resp.text
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    detail = payload.get("detail") or detail
            except Exception:
                pass
            raise MirrorError(resp.status_code, detail or "(no detail)", path)

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "base_url": self.base_url,
            "reachable": False,
            "auth_enabled": None,
        }
        try:
            data = await self._request("GET", "/auth/status")
            out["reachable"] = True
            if isinstance(data, dict):
                out["auth_enabled"] = bool(data.get("auth_enabled"))
        except MirrorError as exc:
            out["error"] = str(exc)
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    async def create_notebook(self, name: str, description: str = "") -> dict[str, Any]:
        return await self._request(
            "POST",
            "/notebooks",
            json_body={"name": name, "description": description},
        )

    async def create_source_link(
        self,
        notebook_id: str,
        url: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "link",
            "notebooks": [notebook_id],
            "url": url,
            "embed": False,
        }
        if title:
            body["title"] = title
        # NOTE: ``/api/sources`` accepts multipart/form-data (file uploads);
        # for JSON link/text sources the open-notebook API expects
        # ``/api/sources/json``. Sending JSON to ``/api/sources`` returns
        # 422 with ``body.type: Field required`` because the multipart
        # parser drops the JSON body.
        return await self._request("POST", "/sources/json", json_body=body)

    async def create_note(
        self,
        notebook_id: str,
        content: str,
        title: str | None = None,
        note_type: str = "ai",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content, "note_type": note_type}
        if title:
            body["title"] = title
        body["notebook_id"] = notebook_id
        return await self._request("POST", "/notes", json_body=body)

    async def list_notebooks(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/notebooks")


# --- Result formatting -------------------------------------------------------


def _to_json(obj: Any) -> str:
    """JSON dump with a fallback for non-serialisable objects."""
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _project_to_dict(project: ResearchProject) -> dict[str, Any]:
    """Pydantic model → plain dict (JSON-roundtrip-safe)."""
    return project.model_dump(mode="json")


def _is_url(s: str) -> bool:
    """Return True if ``s`` looks like an http(s):// URL (mirrorable)."""
    if not isinstance(s, str):
        return False
    return bool(re.match(r"^https?://", s, re.IGNORECASE))


# --- MCP server --------------------------------------------------------------

mcp = FastMCP("research_project")


# ---- Discovery --------------------------------------------------------------


@mcp.tool()
async def rp_health() -> str:
    """Probe both storage and the open-notebook mirror.

    Returns ``{storage_root, open_notebook: {reachable, base_url,
    auth_enabled, notebook_count}, version}``. Use this before a long
    workflow to confirm both backends are healthy.
    """
    storage = str(_storage_root())
    mirror = OpenNotebookMirror(timeout_s=HEALTH_TIMEOUT_S)
    try:
        ob = await mirror.health()
        # Optional notebook count — best-effort.
        if ob.get("reachable"):
            try:
                nbs = await mirror.list_notebooks()
                ob["notebook_count"] = len(nbs) if isinstance(nbs, list) else None
            except Exception:
                ob["notebook_count"] = None
        return _to_json(
            {
                "storage_root": storage,
                "open_notebook": ob,
                "version": __version__,
            }
        )
    finally:
        await mirror.aclose()


@mcp.tool()
async def rp_list_projects(status: str = None) -> str:
    """List all research projects (cheap; reads the registry).

    Args:
        status: Optional filter — ``"active"``, ``"paused"``,
            ``"concluded"``, ``"archived"``. Omit for all.

    Returns:
        A list of ``{slug, title, status, notebook_id, last_active, tags}``.
    """
    try:
        items = _list_projects_on_disk(_storage_root(), status=status)
        return _to_json({"projects": items, "count": len(items)})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def rp_get_project(slug: str) -> str:
    """Fetch the full canonical state of a project by slug.

    Args:
        slug: Project slug (e.g. ``"silver-comex-inventory"``).

    Returns:
        The full ``ResearchProject`` JSON, or ``{"error": "..."}`` if
        the slug is unknown.
    """
    try:
        project = load_project(_storage_root(), slug)
        return _to_json(_project_to_dict(project))
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})


# ---- Create / update --------------------------------------------------------


@mcp.tool()
async def rp_create_project(
    slug: str,
    title: str,
    scope: str,
    tags: list = None,
    initial_hypotheses: list = None,
    initial_questions: list = None,
) -> str:
    """Create a new research project on disk + mirror to open-notebook.

    The on-disk state.json is written FIRST so the project is recoverable
    even if open-notebook is unreachable. If the mirror is reachable, a
    notebook is created and ``notebook_id`` is set; if not, the project
    is still created with ``notebook_id=None`` and a ``warning`` field
    is returned alongside the project.

    Args:
        slug: Stable project id (``"lowercase-with-hyphens"``).
        title: Human-readable title.
        scope: The question or scope the project addresses.
        tags: Optional list of tags.
        initial_hypotheses: Optional list of ``{id, claim, confidence,
            reasoning?}`` dicts. Auto-IDs (``"H1"`` etc.) are NOT
            applied — caller-supplied IDs are honored.
        initial_questions: Optional list of question strings; IDs are
            auto-assigned (``"Q1"``, ``"Q2"`` ...).

    Returns:
        ``{project, notebook_id, warning?}``. ``warning`` is non-null
        only when the open-notebook mirror failed.
    """
    warning: str | None = None
    notebook_id: str | None = None

    # 1. Mirror first — if it works, capture the id; if it fails, note it
    # but proceed to write the canonical state.
    mirror = OpenNotebookMirror()
    try:
        nb = await mirror.create_notebook(
            name=f"[rp] {slug}",
            description=f"{title}\n\n{scope}",
        )
        if isinstance(nb, dict):
            notebook_id = nb.get("notebook_id") or nb.get("id")
            if isinstance(nb, dict) and "notebook_id" not in nb and "id" in nb:
                # open-notebook may return bare id; normalise to 'notebook:<id>'.
                raw_id = nb.get("id")
                if isinstance(raw_id, str) and not raw_id.startswith("notebook:"):
                    notebook_id = f"notebook:{raw_id}"
    except Exception as exc:
        warning = f"open-notebook mirror unavailable: {type(exc).__name__}: {exc}"
    finally:
        await mirror.aclose()

    # 2. Write canonical state.
    try:
        project = _create_project_on_disk(
            _storage_root(),
            slug=slug,
            title=title,
            scope=scope,
            notebook_id=notebook_id,
            tags=tags,
            initial_hypotheses=initial_hypotheses,
            initial_questions=initial_questions,
        )
    except ProjectAlreadyExistsError as exc:
        return _to_json({"error": str(exc), "slug": slug})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})

    out: dict[str, Any] = {
        "project": _project_to_dict(project),
        "notebook_id": notebook_id,
    }
    if warning:
        out["warning"] = warning
    return _to_json(out)


@mcp.tool()
async def rp_update_hypothesis(
    slug: str,
    hypothesis_id: str,
    confidence: float = None,
    reasoning: str = None,
    claim: str = None,
) -> str:
    """Update an existing hypothesis. Logs the change to the timeline.

    Args:
        slug: Project slug.
        hypothesis_id: Hypothesis id (e.g. ``"H1"``).
        confidence: New confidence in [0.0, 1.0]; omit to leave unchanged.
        reasoning: New reasoning text; omit to leave unchanged.
        claim: New claim text; omit to leave unchanged.

    Returns:
        ``{hypothesis, project}`` with the updated hypothesis and full
        project state, or ``{"error": "..."}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    target = next((h for h in project.hypotheses if h.id == hypothesis_id), None)
    if target is None:
        return _to_json(
            {
                "error": f"no hypothesis {hypothesis_id!r} in project {slug!r}",
                "slug": slug,
                "hypothesis_id": hypothesis_id,
            }
        )

    changes: list[str] = []
    if confidence is not None:
        if not 0.0 <= confidence <= 1.0:
            return _to_json(
                {"error": f"confidence must be in [0.0, 1.0]; got {confidence}"}
            )
        if target.confidence != confidence:
            changes.append(f"confidence {target.confidence} → {confidence}")
        target.confidence = confidence
    if reasoning is not None and target.reasoning != reasoning:
        changes.append("reasoning updated")
        target.reasoning = reasoning
    if claim is not None and target.claim != claim:
        changes.append("claim updated")
        target.claim = claim

    target.last_updated = _utcnow_iso()
    event = (
        f"updated {hypothesis_id}"
        + (f" ({'; '.join(changes)})" if changes else " (no-op)")
    )
    project.timeline.append(TimelineEvent(event=event))

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "hypothesis": _project_to_dict(target),
            "project": _project_to_dict(project),
        }
    )


@mcp.tool()
async def rp_add_hypothesis(
    slug: str,
    claim: str,
    confidence: float,
    reasoning: str = None,
) -> str:
    """Add a new hypothesis to a project. Auto-assigns the next ``H<n>`` id.

    Args:
        slug: Project slug.
        claim: The hypothesis statement.
        confidence: Initial confidence in [0.0, 1.0].
        reasoning: Optional rationale.

    Returns:
        ``{hypothesis, project}`` with the new hypothesis and full project.
    """
    if not 0.0 <= confidence <= 1.0:
        return _to_json({"error": f"confidence must be in [0.0, 1.0]; got {confidence}"})
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    new_id = next_id("H", [h.id for h in project.hypotheses])
    hyp = Hypothesis(id=new_id, claim=claim, confidence=confidence, reasoning=reasoning)
    project.hypotheses.append(hyp)
    project.timeline.append(
        TimelineEvent(event=f"added hypothesis {new_id} (confidence {confidence})")
    )

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "hypothesis": _project_to_dict(hyp),
            "project": _project_to_dict(project),
        }
    )


@mcp.tool()
async def rp_open_question(slug: str, text: str) -> str:
    """Open a new question in the project. Auto-assigns ``Q<n>``.

    Args:
        slug: Project slug.
        text: The question text.

    Returns:
        ``{question, project}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    new_id = next_id("Q", [q.id for q in project.questions])
    q = Question(id=new_id, text=text, status="open")
    project.questions.append(q)
    project.timeline.append(TimelineEvent(event=f"opened question {new_id}"))

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "question": _project_to_dict(q),
            "project": _project_to_dict(project),
        }
    )


@mcp.tool()
async def rp_answer_question(slug: str, question_id: str, answer: str) -> str:
    """Mark a question answered, record the answer + timestamp.

    Args:
        slug: Project slug.
        question_id: Question id (e.g. ``"Q1"``).
        answer: The answer text.

    Returns:
        ``{question, project}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    q = next((qq for qq in project.questions if qq.id == question_id), None)
    if q is None:
        return _to_json(
            {
                "error": f"no question {question_id!r} in project {slug!r}",
                "slug": slug,
                "question_id": question_id,
            }
        )

    q.status = "answered"
    q.answer = answer
    q.answered = _utcnow_iso()
    project.timeline.append(
        TimelineEvent(event=f"answered {question_id}")
    )

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "question": _project_to_dict(q),
            "project": _project_to_dict(project),
        }
    )


@mcp.tool()
async def rp_mark_dead_end(slug: str, description: str) -> str:
    """Record a dead-end path so future research doesn't re-do it.

    Args:
        slug: Project slug.
        description: What was tried and why it didn't work.

    Returns:
        ``{dead_end, project}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    new_id = next_id("DE", [d.id for d in project.dead_ends])
    de = DeadEnd(id=new_id, description=description)
    project.dead_ends.append(de)
    project.timeline.append(TimelineEvent(event=f"recorded dead-end {new_id}"))

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "dead_end": _project_to_dict(de),
            "project": _project_to_dict(project),
        }
    )


@mcp.tool()
async def rp_add_contradiction(
    slug: str,
    claim_a_id: str,
    claim_b_id: str,
    interpretation: str,
) -> str:
    """Record that two claims/hypotheses disagree.

    Args:
        slug: Project slug.
        claim_a_id: First id (hypothesis ``H<n>`` or evidence ``E<n>``).
        claim_b_id: Second id.
        interpretation: How the contradiction was resolved, or why it stands.

    Returns:
        ``{contradiction, project}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    new_id = next_id("C", [c.id for c in project.contradictions])
    c = Contradiction(
        id=new_id,
        claim_a_id=claim_a_id,
        claim_b_id=claim_b_id,
        interpretation=interpretation,
    )
    project.contradictions.append(c)
    project.timeline.append(
        TimelineEvent(event=f"added contradiction {new_id} ({claim_a_id} ↔ {claim_b_id})")
    )

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "contradiction": _project_to_dict(c),
            "project": _project_to_dict(project),
        }
    )


# ---- Evidence (the key one) -------------------------------------------------


@mcp.tool()
async def rp_add_evidence(
    slug: str,
    claim: str,
    sources: list,
    weight: float,
    source_types: list = None,
    note: str = None,
    evidence_id: str = None,
) -> str:
    """Add a piece of evidence to a project and mirror URLs to open-notebook.

    On-disk write happens FIRST (canonical). Then, for every URL in
    ``sources``, the tool attempts ``create_source_link`` against the
    project's notebook so Bill can click through. If the mirror is
    unreachable, the canonical evidence is still saved and a ``warning``
    is returned.

    Args:
        slug: Project slug.
        claim: The evidence statement.
        sources: List of source URLs (and/or short citations).
        weight: Credibility weight in [0.0, 1.0] (``1.0`` = peer-reviewed,
            ``0.0`` = untrusted rumor).
        source_types: Optional list of source-type tags aligned with
            ``sources`` (e.g. ``["primary", "cme-bulletin"]``).
        note: Optional context.
        evidence_id: Optional explicit id (default: auto ``E<n>``).

    Returns:
        ``{evidence, project, mirror_warnings?}``. ``mirror_warnings``
        is a list of failure strings when individual source-link
        mirrors failed (but the on-disk write still succeeded).
    """
    if not 0.0 <= weight <= 1.0:
        return _to_json({"error": f"weight must be in [0.0, 1.0]; got {weight}"})
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    eid = evidence_id or next_id("E", [e.id for e in project.evidence])
    ev = Evidence(
        id=eid,
        claim=claim,
        sources=list(sources or []),
        source_types=list(source_types or []),
        weight=weight,
        note=note,
    )
    project.evidence.append(ev)
    project.timeline.append(
        TimelineEvent(event=f"added evidence {eid} (weight {weight})")
    )

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    # 2. Mirror URLs to open-notebook — best-effort, per-URL.
    mirror_warnings: list[str] = []
    if not project.notebook_id:
        # No notebook was ever created (mirror was down at project creation,
        # or the project was loaded from disk without one). Surface this
        # explicitly so callers know URLs aren't browsable in open-notebook.
        if sources:
            mirror_warnings.append(
                "no open-notebook notebook exists for this project; "
                "URLs are recorded in canonical state but not browsable. "
                "Re-create the project (rp_create_project) once the mirror is "
                "reachable to backfill the notebook."
            )
    elif sources:
        mirror = OpenNotebookMirror()
        try:
            for url in sources:
                if not _is_url(url):
                    continue
                try:
                    await mirror.create_source_link(
                        notebook_id=project.notebook_id,
                        url=url,
                        title=f"{eid}: {claim[:80]}",
                    )
                except Exception as exc:
                    mirror_warnings.append(
                        f"{url}: {type(exc).__name__}: {exc}"
                    )
        finally:
            await mirror.aclose()

    out: dict[str, Any] = {
        "evidence": _project_to_dict(ev),
        "project": _project_to_dict(project),
    }
    if mirror_warnings:
        out["mirror_warnings"] = mirror_warnings
    return _to_json(out)


# ---- Read / report ----------------------------------------------------------


@mcp.tool()
async def rp_query_project(
    slug: str,
    max_evidence: int = 20,
    max_questions: int = 20,
) -> str:
    """Return a compact structured summary of a project.

    Designed for cheap context injection: title, status, hypothesis
    confidence list, open-question count, most-recent evidence, most-
    recent timeline events, and contradictions.

    Args:
        slug: Project slug.
        max_evidence: Cap on recent evidence items to include.
        max_questions: Cap on open questions to include (answered ones
            are counted only).

    Returns:
        ``{slug, title, status, notebook_id, last_active, confidence_overall,
        hypotheses: [...], open_question_count, open_questions: [...],
        recent_evidence: [...], recent_timeline: [...],
        contradictions: [...]}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    open_qs = [q for q in project.questions if q.status == "open"]
    recent_ev = list(reversed(project.evidence))[:max_evidence]
    recent_tl = list(reversed(project.timeline))[:max_evidence]

    return _to_json(
        {
            "slug": project.id,
            "title": project.title,
            "status": project.status,
            "notebook_id": project.notebook_id,
            "last_active": project.last_active,
            "last_session": project.last_session,
            "confidence_overall": project.confidence_overall(),
            "hypotheses": [
                {
                    "id": h.id,
                    "claim": h.claim,
                    "confidence": h.confidence,
                    "last_updated": h.last_updated,
                }
                for h in project.hypotheses
            ],
            "open_question_count": len(open_qs),
            "open_questions": [
                {"id": q.id, "text": q.text, "opened": q.opened}
                for q in open_qs[:max_questions]
            ],
            "evidence_count": len(project.evidence),
            "recent_evidence": [
                {
                    "id": e.id,
                    "claim": e.claim,
                    "weight": e.weight,
                    "sources": e.sources,
                    "added": e.added,
                }
                for e in recent_ev
            ],
            "contradiction_count": len(project.contradictions),
            "contradictions": [_project_to_dict(c) for c in project.contradictions],
            "dead_end_count": len(project.dead_ends),
            "recent_timeline": [
                {"timestamp": t.timestamp, "event": t.event, "kind": t.kind}
                for t in recent_tl
            ],
        }
    )


@mcp.tool()
async def rp_sync_into_context(max_projects: int = 5, status: str = "active") -> str:
    """Build a compact multi-line block suitable for system-prompt injection.

    This is THE magic tool — it produces a low-token summary of every
    active project (title, hypothesis confidences, open-question count,
    last touched) that can be prepended to the system prompt so the
    agent always knows what research it has in flight.

    Args:
        max_projects: Cap on number of projects to include (most-recent
            first by ``last_active``).
        status: Project status filter (default ``"active"``).

    Returns:
        A JSON string with ``{context_block: "<multi-line text>",
        project_count: N}``. The ``context_block`` is plain text and
        can be injected verbatim.
    """
    try:
        items = _list_projects_on_disk(_storage_root(), status=status)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    items_sorted = sorted(
        items, key=lambda d: d.get("last_active") or "", reverse=True
    )[:max_projects]

    lines: list[str] = []
    lines.append(f"# Active research projects ({len(items_sorted)})")
    if not items_sorted:
        lines.append("(none)")
        return _to_json(
            {"context_block": "\n".join(lines), "project_count": 0}
        )

    for meta in items_sorted:
        slug = meta.get("slug", "")
        title = meta.get("title", "")
        last_active = meta.get("last_active", "")
        tags = meta.get("tags") or []

        # Pull the project for hypothesis/question detail — but tolerate
        # missing/corrupt files so the block is still useful.
        try:
            p = load_project(_storage_root(), slug)
            confs = ", ".join(
                f"{h.id}={h.confidence:.2f}" for h in p.hypotheses
            ) or "(no hypotheses)"
            open_count = sum(1 for q in p.questions if q.status == "open")
            ev_count = len(p.evidence)
        except Exception:
            confs = "(load failed)"
            open_count = 0
            ev_count = 0

        lines.append(f"\n## {slug} — {title}")
        if tags:
            lines.append(f"tags: {', '.join(tags)}")
        lines.append(f"last active: {last_active}")
        lines.append(f"hypotheses: {confs}")
        lines.append(
            f"open questions: {open_count}; evidence items: {ev_count}"
        )

    return _to_json(
        {
            "context_block": "\n".join(lines),
            "project_count": len(items_sorted),
        }
    )


@mcp.tool()
async def rp_render_report(slug: str, format: str = "markdown") -> str:
    """Render a full memo of a project. Markdown by default.

    Sections: intro (title, scope, status, last active); hypotheses with
    reasoning; evidence grouped by id; contradictions; open questions;
    dead-ends; timeline.

    Args:
        slug: Project slug.
        format: ``"markdown"`` (default) or ``"json"``. ``"json"``
            returns the structured memo as a single JSON object.

    Returns:
        ``{slug, format, report}``. For ``"markdown"`` the report is a
        string; for ``"json"`` it's a dict.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    if format not in ("markdown", "json"):
        return _to_json({"error": f"format must be 'markdown' or 'json'; got {format!r}"})

    if format == "json":
        memo: dict[str, Any] = {
            "intro": {
                "slug": project.id,
                "title": project.title,
                "scope": project.scope,
                "status": project.status,
                "tags": project.tags,
                "notebook_id": project.notebook_id,
                "created": project.created,
                "last_active": project.last_active,
                "last_session": project.last_session,
                "confidence_overall": project.confidence_overall(),
            },
            "hypotheses": [_project_to_dict(h) for h in project.hypotheses],
            "evidence": [_project_to_dict(e) for e in project.evidence],
            "contradictions": [_project_to_dict(c) for c in project.contradictions],
            "questions": [_project_to_dict(q) for q in project.questions],
            "dead_ends": [_project_to_dict(d) for d in project.dead_ends],
            "timeline": [_project_to_dict(t) for t in project.timeline],
        }
        return _to_json({"slug": slug, "format": "json", "report": memo})

    # Markdown memo.
    lines: list[str] = []
    lines.append(f"# {project.title}")
    lines.append("")
    lines.append(f"_slug: `{project.id}` — status: **{project.status}**_")
    lines.append(f"_last active: {project.last_active}_")
    if project.notebook_id:
        lines.append(f"_open-notebook mirror: `{project.notebook_id}`_")
    if project.tags:
        lines.append(f"_tags: {', '.join(project.tags)}_")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(project.scope or "_(no scope)_")
    lines.append("")

    agg = project.confidence_overall()
    if project.hypotheses:
        lines.append(
            f"**Overall confidence:** {agg:.2f} "
            f"(mean of {len(project.hypotheses)} hypotheses)"
        )
        lines.append("")

    if project.hypotheses:
        lines.append("## Hypotheses")
        lines.append("")
        for h in project.hypotheses:
            lines.append(f"### {h.id} — confidence {h.confidence:.2f}")
            lines.append("")
            lines.append(f"**Claim:** {h.claim}")
            if h.reasoning:
                lines.append("")
                lines.append(f"**Reasoning:** {h.reasoning}")
            lines.append(f"_last updated: {h.last_updated}_")
            lines.append("")

    if project.evidence:
        lines.append("## Evidence")
        lines.append("")
        for e in project.evidence:
            lines.append(f"### {e.id} — weight {e.weight:.2f}")
            lines.append("")
            lines.append(f"**Claim:** {e.claim}")
            if e.sources:
                src_lines = []
                for i, s in enumerate(e.sources):
                    label = s
                    if e.source_types and i < len(e.source_types):
                        label = f"[{e.source_types[i]}] {s}"
                    src_lines.append(f"- {label}")
                lines.append("")
                lines.append("**Sources:**")
                lines.extend(src_lines)
            if e.note:
                lines.append("")
                lines.append(f"**Note:** {e.note}")
            lines.append(f"_added: {e.added}_")
            lines.append("")

    if project.contradictions:
        lines.append("## Contradictions")
        lines.append("")
        for c in project.contradictions:
            lines.append(
                f"- **{c.id}** — {c.claim_a_id} ↔ {c.claim_b_id}: {c.interpretation}"
            )
        lines.append("")

    if project.questions:
        lines.append("## Questions")
        lines.append("")
        for q in project.questions:
            tag = f" *({q.status})*" if q.status != "open" else ""
            lines.append(f"- **{q.id}{tag}** — {q.text}")
            if q.answer:
                lines.append(f"  - **Answer:** {q.answer}")
        lines.append("")

    if project.dead_ends:
        lines.append("## Dead ends")
        lines.append("")
        for d in project.dead_ends:
            lines.append(f"- **{d.id}** — {d.description}")
        lines.append("")

    if project.timeline:
        lines.append("## Timeline")
        lines.append("")
        for t in project.timeline:
            kind_tag = f" _({t.kind})_" if t.kind != "auto" else ""
            lines.append(f"- {t.timestamp} — {t.event}{kind_tag}")
        lines.append("")

    return _to_json(
        {"slug": slug, "format": "markdown", "report": "\n".join(lines)}
    )


# ---- Meta -------------------------------------------------------------------


@mcp.tool()
async def rp_archive_project(slug: str) -> str:
    """Mark a project archived. State is preserved.

    Args:
        slug: Project slug.

    Returns:
        ``{project}`` with the archived project, or ``{"error": "..."}``.
    """
    try:
        project = archive_project(_storage_root(), slug)
        return _to_json({"project": _project_to_dict(project)})
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})


@mcp.tool()
async def rp_link_session(slug: str, session_id: str) -> str:
    """Record which Hermes session last touched a project.

    Sets ``project.last_session`` and bumps ``project.last_active``.

    Args:
        slug: Project slug.
        session_id: Hermes session identifier.

    Returns:
        ``{project}``.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    project.last_session = session_id
    project.last_active = _utcnow_iso()
    project.timeline.append(
        TimelineEvent(event=f"linked to session {session_id}")
    )

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json({"project": _project_to_dict(project)})


@mcp.tool()
async def rp_manual_override(
    slug: str,
    field_path: str,
    new_value,
    reason: str,
) -> str:
    """Apply a typed manual override to any project field by dot-path.

    Used by Bill to correct the agent. Logs a ``kind="manual"`` event to
    the timeline with the reason. Validates the new value against the
    field's Pydantic type before writing.

    Supported path forms:

        ``scope``                               → top-level scalar
        ``tags``                                → top-level list (replace)
        ``hypotheses.H1.confidence``            → list item by id
        ``evidence.E2.weight``                  → list item by id
        ``questions.Q3.answer``                 → list item by id
        ``hypotheses.1.claim``                  → list item by numeric index

    Args:
        slug: Project slug.
        field_path: Dot-notation path to the field.
        new_value: New value (must match the field's schema).
        reason: Why the override is being made; written to the timeline.

    Returns:
        ``{project, old_value, new_value, field_path}`` on success;
        ``{"error": "..."}`` on validation failure.
    """
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})

    # Parse path → list of segments.
    segments = [s for s in field_path.split(".") if s]
    if not segments:
        return _to_json({"error": "field_path must be non-empty", "field_path": field_path})

    # Navigate to the parent container + final field name.
    container: Any = project
    old_value: Any = None
    list_index: int | None = None  # for list items
    parent_attr: str | None = None  # attribute name on parent (for dict/setattr)

    for i, seg in enumerate(segments[:-1]):
        if isinstance(container, ResearchProject):
            attr = getattr(container, seg, None)
            if attr is None and not hasattr(container, seg):
                return _to_json(
                    {
                        "error": f"unknown field {seg!r} on project",
                        "field_path": field_path,
                    }
                )
            container = attr
            list_index = None
            parent_attr = seg
            continue

        if isinstance(container, list):
            # Try id match first, fall back to int index.
            matched = None
            for j, item in enumerate(container):
                if hasattr(item, "id") and getattr(item, "id", None) == seg:
                    matched = j
                    break
            if matched is None:
                try:
                    matched = int(seg)
                except ValueError:
                    matched = None
            if matched is None or not (0 <= matched < len(container)):
                return _to_json(
                    {
                        "error": f"no list item {seg!r} at path position {i}",
                        "field_path": field_path,
                    }
                )
            list_index = matched
            parent_attr = None
            container = container[matched]
            continue

        return _to_json(
            {
                "error": f"cannot navigate into {type(container).__name__} at segment {seg!r}",
                "field_path": field_path,
            }
        )

    final = segments[-1]

    # Resolve the target (parent + field name).
    if isinstance(container, ResearchProject):
        if not hasattr(container, final):
            return _to_json(
                {"error": f"unknown field {final!r} on project", "field_path": field_path}
            )
        old_value = getattr(container, final)
        # Determine field type from the model's annotation, if available.
        annotation = ResearchProject.model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        try:
            coerced = _coerce_override(new_value, field_type, final)
        except Exception as exc:
            return _to_json(
                {
                    "error": f"validation failed for {final!r}: {exc}",
                    "field_path": field_path,
                    "new_value": new_value,
                }
            )
        setattr(container, final, coerced)
        applied = coerced
    elif isinstance(container, list):
        # Resolve list index for the final segment.
        matched = None
        if list_index is not None:
            matched = list_index
        else:
            for j, item in enumerate(container):
                if hasattr(item, "id") and getattr(item, "id", None) == final:
                    matched = j
                    break
            if matched is None:
                try:
                    matched = int(final)
                except ValueError:
                    matched = None
        if matched is None or not (0 <= matched < len(container)):
            return _to_json(
                {
                    "error": f"cannot resolve list item {final!r}",
                    "field_path": field_path,
                }
            )
        item = container[matched]
        if not hasattr(item, final):
            return _to_json(
                {"error": f"unknown field {final!r} on {type(item).__name__}",
                 "field_path": field_path}
            )
        old_value = getattr(item, final)
        annotation = type(item).model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        try:
            coerced = _coerce_override(new_value, field_type, final)
        except Exception as exc:
            return _to_json(
                {
                    "error": f"validation failed for {final!r}: {exc}",
                    "field_path": field_path,
                    "new_value": new_value,
                }
            )
        setattr(item, final, coerced)
        applied = coerced
    elif isinstance(container, BaseModel):
        # Loop ended on a nested Pydantic item (e.g. Hypothesis reached via
        # list[id] or list[int-index]). Treat it like the project root but
        # use the item's own model_fields for type coercion.
        if not hasattr(container, final):
            return _to_json(
                {
                    "error": f"unknown field {final!r} on {type(container).__name__}",
                    "field_path": field_path,
                }
            )
        old_value = getattr(container, final)
        annotation = type(container).model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        try:
            coerced = _coerce_override(new_value, field_type, final)
        except Exception as exc:
            return _to_json(
                {
                    "error": f"validation failed for {final!r}: {exc}",
                    "field_path": field_path,
                    "new_value": new_value,
                }
            )
        setattr(container, final, coerced)
        applied = coerced
    else:
        return _to_json(
            {
                "error": f"unsupported container type {type(container).__name__}",
                "field_path": field_path,
            }
        )

    # Log the manual override.
    summary_old = _summarize_value(old_value)
    summary_new = _summarize_value(applied)
    project.timeline.append(
        TimelineEvent(
            event=(
                f"manual override {field_path}: {summary_old} → {summary_new} "
                f"(reason: {reason})"
            ),
            kind="manual",
        )
    )
    project.last_active = _utcnow_iso()

    try:
        save_project(_storage_root(), project)
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})

    return _to_json(
        {
            "project": _project_to_dict(project),
            "field_path": field_path,
            "old_value": old_value,
            "new_value": applied,
        }
    )


# --- Override helpers --------------------------------------------------------


def _coerce_override(value: Any, field_type: Any, field_name: str) -> Any:
    """Best-effort coercion of ``value`` to ``field_type`` for override writes.

    Uses ``ResearchProject.model_fields``/``model.model_fields`` annotations
    when available; falls back to a small set of primitives. Raises
    ``ValueError`` for unsupported types so the caller can surface a
    friendly error message.
    """
    # Strings: special-case enums/literals handled by Pydantic upstream; here
    # we just pass through to the parent validator by re-creating the value.
    if field_type is None:
        return value

    # If the type is Optional[X], unwrap.
    origin = getattr(field_type, "__origin__", None)
    if origin is None and hasattr(field_type, "__args__"):
        # typing.Union / Optional
        args = field_type.__args__
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            field_type = non_none[0]
            origin = getattr(field_type, "__origin__", None)

    # bool (int subclass — must check first).
    if field_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes"):
                return True
            if value.lower() in ("false", "0", "no", ""):
                return False
        raise ValueError(f"cannot coerce {value!r} to bool")

    # int / float / str.
    if field_type in (int, float, str):
        try:
            return field_type(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cannot coerce {value!r} to {field_type.__name__}: {exc}") from exc

    # list[X] (origin is list)
    if origin is list:
        if not isinstance(value, list):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return list(value)

    # For Pydantic models and richer types, defer to Pydantic's TypeAdapter.
    try:
        from pydantic import TypeAdapter  # type: ignore

        adapter = TypeAdapter(field_type)
        return adapter.validate_python(value)
    except Exception:
        # Last-resort: pass through.
        return value


def _summarize_value(v: Any) -> str:
    """Short, single-line summary of a value for the timeline."""
    if v is None:
        return "None"
    if isinstance(v, str):
        s = v.replace("\n", " ").strip()
        return s if len(s) <= 60 else s[:57] + "..."
    if isinstance(v, (int, float, bool)):
        return repr(v)
    if isinstance(v, list):
        return f"list(len={len(v)})"
    if isinstance(v, dict):
        return f"dict(keys={len(v)})"
    s = str(v).replace("\n", " ").strip()
    return s if len(s) <= 60 else s[:57] + "..."


# ---- Synthesis (multi-source answer with inline credibility) --------------


def _score_source_inline(url: str) -> str | None:
    """Call source-credibility-mcp via stdio JSON-RPC to score one URL.

    Returns the inline badge string ``[host — 0.72 (tier)]`` or ``None``
    on any failure (synthesis must never block on a missing scorer).
    """
    if not _is_url(url):
        return None
    exe = (
        Path(os.environ.get("HERMES_VENV") or r"C:\Data\Hermes\hermes-agent\venv")
        / "Scripts"
        / "source-credibility-mcp.exe"
    )
    if not exe.exists():
        return None
    try:
        proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception:
        return None
    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "research-project-synth", "version": __version__},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "cred_score_source",
                        "arguments": {"url": url},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            stdout, _ = proc.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            return None
        # Last non-empty JSON line is the tools/call result.
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != 2:
                continue
            content = (
                msg.get("result", {}).get("content", [])
                if isinstance(msg.get("result"), dict)
                else []
            )
            if not content:
                return None
            text = content[0].get("text", "") if isinstance(content[0], dict) else ""
            if not text:
                return None
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return None
            return payload.get("inline_badge") or payload.get("badge")
    except Exception:
        return None
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def _synthesis_relevance_score(text: str, question: str) -> float:
    """Lightweight keyword-overlap score between ``text`` and ``question``.

    Used to rank evidence items against the user's question. NOT an LLM
    call — just enough to pick the most-relevant items before scoring
    them with cred_score_source.
    """
    if not text or not question:
        return 0.0
    text_l = text.lower()
    q_tokens = [t for t in re.split(r"\W+", question.lower()) if len(t) >= 3]
    if not q_tokens:
        return 0.0
    hits = sum(1 for t in q_tokens if t in text_l)
    return hits / len(q_tokens)


def _format_badge_line(
    url: str, weight: float | None = None, inline_badge: str | None = None
) -> str:
    """Render one source line with the inline credibility badge."""
    badge = inline_badge or _score_source_inline(url) or ""
    if weight is not None:
        return f"- {url} — weight {weight:.2f} {badge}".rstrip()
    return f"- {url} {badge}".rstrip()


@mcp.tool()
async def rp_synthesize_answer(
    slug: str,
    question: str,
    max_sources: int = 8,
    include_contradictions: bool = True,
    focus_hypothesis_ids: list = None,
    log_to_project: bool = True,
) -> str:
    """Synthesize a multi-source answer for a question against one project.

    This is the "one-call research answer" tool. It:

    1. Loads the project state from disk (canonical).
    2. Ranks evidence items by relevance to ``question``.
    3. Scores each evidence item's source URLs via the
       ``source-credibility-mcp`` scorer (``cred_score_source``) — that
       returns an inline badge like ``[cmegroup.com — 0.92 (primary)]``.
    4. Pulls supporting context from the open-notebook mirror
       (best-effort — fails silently if unreachable).
    5. Flags contradictions touching the question or focus hypotheses.
    6. Lists open questions + suggests concrete follow-up evidence
       items the agent should hunt for next.
    7. Optionally logs the synthesis to the project's timeline so
       later sessions see "answered <question>" with a short pointer
       to where the full synthesis was delivered.
    8. Returns a JSON envelope with a ``memo`` (markdown) and a
       ``dossier`` (structured) the calling agent can quote or
       post-process.

    The prose synthesis itself is written by the calling agent — this
    tool produces the evidence + scoring + structure; the agent does
    the language.

    Args:
        slug: Project slug (e.g. ``"silver-comex-inventory"``).
        question: The research question to answer.
        max_sources: Cap on evidence items returned (most-relevant
            first). Default 8.
        include_contradictions: When True (default), include any
            contradictions touching a focus hypothesis or the
            question keywords.
        focus_hypothesis_ids: Optional list of hypothesis IDs to
            concentrate on (e.g. ``["H2", "H3"]``). When omitted,
            every hypothesis is considered.
        log_to_project: When True (default), append a timeline event
            so the synthesis shows up in the project's history.

    Returns:
        ``{question, slug, scope, confidence_overall, memo, dossier,
        warnings}`` where:
        - ``memo`` is a markdown memo the agent can deliver as-is.
        - ``dossier`` is structured: ``{focus_hypotheses,
          evidence_ranked, contradictions, open_questions,
          follow_up_suggestions, notebook_chunks?}``.
        - ``warnings`` lists any non-fatal errors (e.g. source-credibility
          unreachable, open-notebook unreachable).
    """
    warnings: list[str] = []

    # 1. Load project.
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})

    # 2. Rank evidence by relevance to the question.
    focus_set = set(focus_hypothesis_ids or [])
    considered_hyp_ids = (
        {h.id for h in project.hypotheses if h.id in focus_set}
        if focus_set
        else {h.id for h in project.hypotheses}
    )

    scored: list[tuple[float, Evidence]] = []
    for e in project.evidence:
        relevance = _synthesis_relevance_score(
            f"{e.claim}\n{e.note or ''}", question
        )
        # Light bonus if the question shares words with the hypothesis
        # this evidence backs (if any). We don't track that directly,
        # so use overall hypothesis relevance.
        scored.append((relevance, e))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:max_sources]

    # 3. For each top evidence, score its source URLs.
    evidence_ranked: list[dict[str, Any]] = []
    for relevance, e in top:
        scored_sources: list[dict[str, Any]] = []
        for url in e.sources:
            if not _is_url(url):
                continue
            badge = _score_source_inline(url)
            scored_sources.append(
                {"url": url, "inline_badge": badge, "weight": e.weight}
            )
        evidence_ranked.append(
            {
                "evidence_id": e.id,
                "claim": e.claim,
                "relevance": round(relevance, 3),
                "weight": e.weight,
                "added": e.added,
                "note": e.note,
                "sources": scored_sources,
            }
        )

    # 4. Best-effort open-notebook context. Pull a small chunk of the
    # project's notebook search hits for the question so the agent
    # can quote source material if needed.
    notebook_chunks: list[dict[str, Any]] = []
    if project.notebook_id and question.strip():
        mirror = OpenNotebookMirror()
        try:
            payload = {"query": question, "type": "text", "limit": 5}
            if project.notebook_id:
                payload["notebook_id"] = project.notebook_id
            try:
                resp = await mirror._request(  # type: ignore[attr-defined]
                    "POST", "/search", json_body=payload
                )
            except MirrorError as exc:
                warnings.append(f"open-notebook search: {exc}")
            except Exception as exc:
                warnings.append(
                    f"open-notebook search: {type(exc).__name__}: {exc}"
                )
            else:
                if isinstance(resp, list):
                    candidates = resp
                elif isinstance(resp, dict):
                    candidates = (
                        resp.get("results")
                        or resp.get("hits")
                        or resp.get("items")
                        or []
                    )
                else:
                    candidates = []
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    notebook_chunks.append(
                        {
                            "title": item.get("title")
                            or item.get("name")
                            or "",
                            "snippet": (
                                item.get("snippet")
                                or item.get("content_preview")
                                or item.get("text")
                                or ""
                            )[:400],
                            "source_id": item.get("id") or item.get("source_id"),
                            "url": item.get("url"),
                        }
                    )
        finally:
            await mirror.aclose()

    # 5. Contradictions touching the question or focus hypotheses.
    relevant_contradictions: list[dict[str, Any]] = []
    if include_contradictions:
        for c in project.contradictions:
            touched = (
                c.claim_a_id in considered_hyp_ids
                or c.claim_b_id in considered_hyp_ids
                or _synthesis_relevance_score(c.interpretation, question) >= 0.25
            )
            if touched:
                relevant_contradictions.append(_project_to_dict(c))

    # 6. Open questions + follow-up suggestions.
    open_qs = [q for q in project.questions if q.status == "open"]
    follow_up: list[str] = []
    # Suggest evidence to hunt for — anything that's been claimed but
    # has weight <0.6 OR no corroboration.
    for e in project.evidence:
        if e.weight < 0.6 and not e.source_types:
            follow_up.append(
                f"find primary corroboration for {e.id} ({e.claim[:80]}...)"
            )
    # Suggest hypothesis updates — any hyp with confidence <0.5 or no
    # evidence in 14+ days.
    for h in project.hypotheses:
        if h.confidence < 0.5:
            follow_up.append(
                f"decide {h.id} ({h.claim[:80]}...) — confidence {h.confidence:.2f}"
            )
        if h.last_updated:
            try:
                age_days = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(h.last_updated.replace("Z", "+00:00"))
                ).days
            except Exception:
                age_days = 0
            if age_days >= 14:
                follow_up.append(
                    f"revisit {h.id} — last updated {age_days}d ago"
                )

    # 7. Build the memo (markdown the agent can deliver as-is).
    lines: list[str] = []
    lines.append(f"# Synthesis — {project.title}")
    lines.append("")
    lines.append(f"**Question:** {question}")
    lines.append(f"**Project:** `{slug}` — scope: {project.scope[:120]}")
    lines.append(
        f"**Confidence (project mean):** "
        f"{project.confidence_overall() if project.confidence_overall() is not None else '—'}"
    )
    lines.append("")
    if considered_hyp_ids:
        lines.append(f"**Hypotheses in scope:** {', '.join(sorted(considered_hyp_ids))}")
        lines.append("")
    if relevant_contradictions:
        lines.append("## Contradictions")
        lines.append("")
        for c in relevant_contradictions:
            lines.append(
                f"- **{c['id']}** — {c['claim_a_id']} ↔ {c['claim_b_id']}: "
                f"{c['interpretation']}"
            )
        lines.append("")
    if evidence_ranked:
        lines.append("## Evidence (ranked by relevance)")
        lines.append("")
        for item in evidence_ranked:
            lines.append(
                f"### {item['evidence_id']} — relevance {item['relevance']:.2f} · "
                f"weight {item['weight']:.2f}"
            )
            lines.append("")
            lines.append(item["claim"])
            if item["sources"]:
                lines.append("")
                lines.append("**Sources:**")
                for s in item["sources"]:
                    badge = s.get("inline_badge") or ""
                    lines.append(f"- {s['url']} {badge}".rstrip())
            if item["note"]:
                lines.append("")
                lines.append(f"_Note: {item['note']}_")
            lines.append("")
    if notebook_chunks:
        lines.append("## From open-notebook mirror")
        lines.append("")
        for ch in notebook_chunks:
            title = ch.get("title") or "(untitled)"
            snippet = ch.get("snippet") or ""
            url = ch.get("url") or ""
            lines.append(f"- **{title}**" + (f" — {url}" if url else ""))
            if snippet:
                lines.append(f"  > {snippet[:300]}{'…' if len(snippet) > 300 else ''}")
        lines.append("")
    if open_qs:
        lines.append("## Open questions")
        lines.append("")
        for q in open_qs[:5]:
            lines.append(f"- **{q.id}** — {q.text}")
        lines.append("")
    if follow_up:
        lines.append("## Suggested follow-ups")
        lines.append("")
        for f in follow_up[:8]:
            lines.append(f"- {f}")
        lines.append("")
    memo = "\n".join(lines)

    # 8. Optionally log to timeline AND persist the synthesis as a
    # separate JSON file (append-only audit log) AND mirror to
    # open-notebook as a note in the project's notebook. Saves and
    # mirrors are best-effort — canonical synthesis (memo + dossier)
    # is still returned even if either side fails.
    synthesis_meta: dict[str, Any] | None = None
    if log_to_project:
        try:
            project.timeline.append(
                TimelineEvent(
                    event=(
                        f"synthesized answer to: {question[:120]} "
                        f"({len(evidence_ranked)} evidence items, "
                        f"{len(relevant_contradictions)} contradictions)"
                    )
                )
            )
            project.last_active = _utcnow_iso()
            save_project(_storage_root(), project)
        except Exception as exc:
            warnings.append(f"timeline write: {type(exc).__name__}: {exc}")

        # Persist as a JSON file under syntheses/ — append-only audit log.
        try:
            synth_dossier = {
                "focus_hypotheses": sorted(considered_hyp_ids),
                "evidence_ranked": evidence_ranked,
                "contradictions": relevant_contradictions,
                "open_questions": [
                    {"id": q.id, "text": q.text, "opened": q.opened}
                    for q in open_qs[:5]
                ],
                "follow_up_suggestions": follow_up[:8],
                "notebook_chunks": notebook_chunks,
            }
            synthesis_meta = _save_synthesis_on_disk(
                _storage_root(),
                slug,
                question=question,
                memo=memo,
                dossier=synth_dossier,
                confidence_overall=project.confidence_overall(),
                scope=project.scope,
            )
        except Exception as exc:
            warnings.append(f"synthesis save: {type(exc).__name__}: {exc}")

        # Mirror to open-notebook as a note in the project's notebook.
        if (
            project.notebook_id
            and synthesis_meta
            and synthesis_meta.get("synthesis_id")
        ):
            mirror = OpenNotebookMirror()
            try:
                note_title = (
                    f"🔍 Synthesis: {question[:80]}{'…' if len(question) > 80 else ''} "
                    f"({synthesis_meta['timestamp'][:10]})"
                )
                await mirror.create_note(
                    notebook_id=project.notebook_id,
                    content=memo,
                    title=note_title,
                )
            except Exception as exc:
                warnings.append(
                    f"synthesis mirror: {type(exc).__name__}: {exc}"
                )
            finally:
                await mirror.aclose()

    dossier = {
        "focus_hypotheses": sorted(considered_hyp_ids),
        "evidence_ranked": evidence_ranked,
        "contradictions": relevant_contradictions,
        "open_questions": [
            {"id": q.id, "text": q.text, "opened": q.opened}
            for q in open_qs[:5]
        ],
        "follow_up_suggestions": follow_up[:8],
        "notebook_chunks": notebook_chunks,
    }

    return _to_json(
        {
            "question": question,
            "slug": slug,
            "scope": project.scope,
            "confidence_overall": project.confidence_overall(),
            "memo": memo,
            "dossier": dossier,
            "warnings": warnings,
            "synthesis": synthesis_meta,
        }
    )


# --- Synthesis persistence tools --------------------------------------------


@mcp.tool()
async def rp_save_synthesis(
    slug: str,
    question: str,
    memo: str,
    dossier: dict = None,
    confidence_overall: float = None,
    mirror_to_notebook: bool = True,
) -> str:
    """Persist a synthesis result for later retrieval.

    ``rp_synthesize_answer`` calls this automatically when
    ``log_to_project=True``. Use this tool directly when you've built a
    synthesis in-process (e.g. with the LLM writing the prose) and want
    to capture the result.

    The synthesis is stored as JSON at
    ``<storage_root>/<slug>/syntheses/<timestamp>.json``. The memo body
    is mirrored to the project's open-notebook notebook as a note when
    ``mirror_to_notebook=True`` and the project has a notebook.

    Args:
        slug: Project slug.
        question: The research question the synthesis answers.
        memo: The full synthesis memo (markdown).
        dossier: Optional structured dossier (evidence_ranked,
            contradictions, open_questions, follow_up_suggestions).
        confidence_overall: Optional overall confidence in [0.0, 1.0].
        mirror_to_notebook: When True (default), also create a note in
            the project's open-notebook notebook.

    Returns:
        ``{slug, synthesis_id, timestamp, path, evidence_count,
        contradiction_count, open_question_count, follow_up_count,
        warning?}`` where ``warning`` is non-null only when the
        open-notebook mirror failed.
    """
    warning: str | None = None
    try:
        project = load_project(_storage_root(), slug)
    except ProjectNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})

    try:
        meta = _save_synthesis_on_disk(
            _storage_root(),
            slug,
            question=question,
            memo=memo,
            dossier=dossier or {},
            confidence_overall=confidence_overall,
            scope=project.scope,
        )
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})

    if mirror_to_notebook and project.notebook_id:
        mirror = OpenNotebookMirror()
        try:
            note_title = (
                f"🔍 Synthesis: {question[:80]}{'…' if len(question) > 80 else ''} "
                f"({meta['timestamp'][:10]})"
            )
            await mirror.create_note(
                notebook_id=project.notebook_id,
                content=memo,
                title=note_title,
            )
        except Exception as exc:
            warning = f"open-notebook mirror: {type(exc).__name__}: {exc}"
        finally:
            await mirror.aclose()

    out = dict(meta)
    if warning:
        out["warning"] = warning
    return _to_json(out)


@mcp.tool()
async def rp_list_syntheses(slug: str, limit: int = 20) -> str:
    """List recent syntheses for a project (newest first).

    Returns metadata only (no memo body). Use ``rp_load_synthesis`` to
    fetch the full memo + dossier.

    Args:
        slug: Project slug.
        limit: Cap on results (default 20).

    Returns:
        ``{slug, syntheses: [...], count}``.
    """
    try:
        items = _list_syntheses_on_disk(_storage_root(), slug, limit=limit)
        return _to_json({"slug": slug, "syntheses": items, "count": len(items)})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "slug": slug})


@mcp.tool()
async def rp_list_recent_syntheses(limit: int = 20) -> str:
    """List recent syntheses across every project (newest first).

    Powers the dashboard's "Recent syntheses" panel — a single call
    that doesn't require iterating projects client-side.

    Args:
        limit: Cap on results across all projects (default 20).

    Returns:
        ``{syntheses: [{..., project_slug}], count}``.
    """
    try:
        items = _list_all_syntheses_on_disk(_storage_root(), limit=limit)
        return _to_json({"syntheses": items, "count": len(items)})
    except Exception as exc:
        return _to_json({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def rp_load_synthesis(slug: str, synthesis_id: str) -> str:
    """Load the full synthesis (memo + dossier) by id.

    Args:
        slug: Project slug.
        synthesis_id: The synthesis id (the timestamp from
            ``rp_synthesize_answer`` / ``rp_save_synthesis`` with
            ``:`` and ``.`` replaced by ``-``).

    Returns:
        The full synthesis payload, or ``{"error": "..."}``.
    """
    try:
        return _to_json(_load_synthesis_on_disk(_storage_root(), slug, synthesis_id))
    except FileNotFoundError as exc:
        return _to_json({"error": str(exc), "slug": slug, "synthesis_id": synthesis_id})
    except Exception as exc:
        return _to_json(
            {"error": f"{type(exc).__name__}: {exc}", "slug": slug, "synthesis_id": synthesis_id}
        )


# --- Entrypoint --------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()