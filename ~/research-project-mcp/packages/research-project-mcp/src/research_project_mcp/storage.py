"""Storage layer for research projects.

Each project is a directory under ``<storage_root>/<slug>/`` containing:
    state.json   — the canonical ResearchProject JSON
    notes/       — mirror of structured notes (one per hypothesis, etc.)
    sources/     — open-notebook source id mapping

The registry (``<storage_root>/_registry.json``) maps project slugs to
their open-notebook notebook ids, so the MCP server can look up the
notebook for a project without scanning every project directory.

Atomic writes: every state.json update writes to ``state.json.tmp``
first and then renames — partial writes never corrupt the canonical
state.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .schema import ResearchProject


DEFAULT_STORAGE_ROOT = Path(
    os.environ.get("RESEARCH_PROJECTS_DIR")
    or r"C:\Data\Hermes\research_projects"
)


class ProjectNotFoundError(LookupError):
    """Raised when a slug doesn't match any existing project."""


class ProjectAlreadyExistsError(ValueError):
    """Raised when creating a project with a duplicate slug."""


def _project_dir(root: Path, slug: str) -> Path:
    return root / slug


def _state_path(root: Path, slug: str) -> Path:
    return _project_dir(root, slug) / "state.json"


def _registry_path(root: Path) -> Path:
    return root / "_registry.json"


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` atomically via tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_registry(root: Path) -> dict[str, dict]:
    """Return the project registry (``{slug: {notebook_id, status, ...}}``)."""
    p = _registry_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(root: Path, registry: dict[str, dict]) -> None:
    """Persist the project registry atomically."""
    _atomic_write_json(_registry_path(root), registry)


def list_projects(root: Path, status: Optional[str] = None) -> list[dict]:
    """Return summary dicts for every project (optionally filtered by status).

    Cheap: reads the registry, not every state.json.
    """
    registry = load_registry(root)
    out: list[dict] = []
    for slug, meta in registry.items():
        if status and meta.get("status") != status:
            continue
        out.append({"slug": slug, **meta})
    return out


def project_exists(root: Path, slug: str) -> bool:
    return _state_path(root, slug).exists()


def load_project(root: Path, slug: str) -> ResearchProject:
    """Load a project by slug. Raises ProjectNotFoundError if missing."""
    p = _state_path(root, slug)
    if not p.exists():
        raise ProjectNotFoundError(f"no project with slug {slug!r}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return ResearchProject.model_validate(data)


def save_project(root: Path, project: ResearchProject) -> None:
    """Persist a project atomically and update the registry."""
    data = project.model_dump(mode="json")
    _atomic_write_json(_state_path(root, project.id), data)
    registry = load_registry(root)
    registry[project.id] = {
        "title": project.title,
        "status": project.status,
        "notebook_id": project.notebook_id,
        "last_active": project.last_active,
        "tags": project.tags,
    }
    save_registry(root, registry)


def create_project(
    root: Path,
    slug: str,
    title: str,
    scope: str,
    notebook_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    initial_hypotheses: Optional[list[dict]] = None,
    initial_questions: Optional[list[str]] = None,
    session_id: Optional[str] = None,
) -> ResearchProject:
    """Create a new project on disk. Raises if slug already taken."""
    if project_exists(root, slug):
        raise ProjectAlreadyExistsError(f"project {slug!r} already exists")

    from .schema import Hypothesis, Question, TimelineEvent, _utcnow_iso

    now = _utcnow_iso()
    project = ResearchProject(
        id=slug,
        title=title,
        scope=scope,
        notebook_id=notebook_id,
        tags=tags or [],
        hypotheses=[Hypothesis(**h) for h in (initial_hypotheses or [])],
        questions=[
            Question(id=f"Q{i + 1}", text=q) for i, q in enumerate(initial_questions or [])
        ],
        created=now,
        last_active=now,
    )
    project.timeline.append(
        TimelineEvent(event=f"project created (scope: {scope[:80]})")
    )
    if session_id:
        project.last_session = session_id
    save_project(root, project)
    return project


def delete_project(root: Path, slug: str, keep_files: bool = False) -> None:
    """Remove a project from the registry and (optionally) delete its directory.

    Hard delete is destructive — usually you want ``archive_project`` instead.
    """
    registry = load_registry(root)
    registry.pop(slug, None)
    save_registry(root, registry)
    if not keep_files:
        import shutil

        d = _project_dir(root, slug)
        if d.exists():
            shutil.rmtree(d)


def archive_project(root: Path, slug: str, session_id: Optional[str] = None) -> ResearchProject:
    """Mark a project archived (status=archived). Preserves all state."""
    project = load_project(root, slug)
    project.status = "archived"
    project.touch(f"archived project", session_id=session_id)
    save_project(root, project)
    return project


def render_report(root: Path, slug: str, format: str = "markdown") -> dict:
    """Render a project as markdown (default) or json dict.

    Mirrors ``rp_render_report`` in the MCP. Lives in storage so both the
    MCP server and the dashboard endpoint can call it without spawning a
    subprocess.
    """
    project = load_project(root, slug)
    if format not in ("markdown", "json"):
        raise ValueError(f"format must be 'markdown' or 'json'; got {format!r}")

    if format == "json":
        memo = project.model_dump(mode="json")
        return {"slug": slug, "format": "json", "report": memo}

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

    return {"slug": slug, "format": "markdown", "report": "\n".join(lines)}


def next_id(prefix: str, existing_ids: list[str]) -> str:
    """Return the next free id like 'H3', 'E7' given a list of existing ids."""
    n = 1
    while f"{prefix}{n}" in existing_ids:
        n += 1
    return f"{prefix}{n}"


# --- Synthesis helpers (no subprocess; mirror of __main__._score_source_inline is in __main__) ---


def synthesize_answer(
    root: Path,
    slug: str,
    question: str,
    max_sources: int = 8,
    focus_hypothesis_ids: Optional[list[str]] = None,
) -> dict:
    """Return a lightweight synthesis dossier for one project + question.

    Used by the dashboard's /api/research/synthesis endpoint so it doesn't
    have to spawn the MCP subprocess. The full version (with source-credibility
    badges + open-notebook mirror) lives in ``__main__.rp_synthesize_answer``.
    This one returns the structured ranking + open questions + suggested
    follow-ups without the badge/mirror calls — the dashboard renders the
    raw data and the agent runs the full synthesis tool when it needs badges.

    Returns ``{slug, scope, confidence_overall, ranked_evidence,
    open_questions, follow_up_suggestions}``.
    """
    project = load_project(root, slug)
    focus_set = set(focus_hypothesis_ids or [])

    def _overlap(text: str) -> float:
        if not text or not question:
            return 0.0
        text_l = text.lower()
        q_tokens = [
            t for t in __import__("re").split(r"\W+", question.lower()) if len(t) >= 3
        ]
        if not q_tokens:
            return 0.0
        hits = sum(1 for t in q_tokens if t in text_l)
        return hits / len(q_tokens)

    scored = sorted(
        (
            (_overlap(f"{e.claim}\n{e.note or ''}"), e)
            for e in project.evidence
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    top = scored[:max_sources]

    ranked = [
        {
            "evidence_id": e.id,
            "claim": e.claim,
            "relevance": round(rel, 3),
            "weight": e.weight,
            "added": e.added,
            "sources": list(e.sources),
            "note": e.note,
        }
        for rel, e in top
    ]

    open_qs = [q for q in project.questions if q.status == "open"]
    follow_up: list[str] = []
    for e in project.evidence:
        if e.weight < 0.6 and not e.source_types:
            follow_up.append(
                f"find primary corroboration for {e.id} ({e.claim[:80]}...)"
            )
    for h in project.hypotheses:
        if h.confidence < 0.5 or (focus_set and h.id not in focus_set):
            pass
        if h.confidence < 0.5:
            follow_up.append(
                f"decide {h.id} ({h.claim[:80]}...) — confidence {h.confidence:.2f}"
            )

    return {
        "slug": slug,
        "scope": project.scope,
        "confidence_overall": project.confidence_overall(),
        "ranked_evidence": ranked,
        "open_questions": [
            {"id": q.id, "text": q.text, "opened": q.opened} for q in open_qs[:5]
        ],
        "follow_up_suggestions": follow_up[:8],
    }


# --- Synthesis persistence (append-only audit log of "what was answered") -----
#
# Syntheses are the deliverables of `rp_synthesize_answer`. We persist them
# as JSON files under ``<root>/<slug>/syntheses/<iso-timestamp>.json`` so
# they survive the conversation (the in-tool memo is otherwise throwaway).
# They are NOT part of the project's canonical state.json — that file is
# a hot working set; syntheses are cold audit log.
#
# This separation lets the dashboard surface a "Recent syntheses" panel
# without bloating state.json, and lets open-notebook mirror each synthesis
# as a browsable note in the project's notebook.


def _syntheses_dir(root: Path, slug: str) -> Path:
    return _project_dir(root, slug) / "syntheses"


def _synthesis_filename(timestamp_iso: str) -> str:
    """Convert an ISO timestamp to a filename-safe form.

    ``2026-06-21T14:30:00+00:00`` → ``2026-06-21T14-30-00-00-00.json``.
    Colons are illegal on Windows filesystems.
    """
    safe = timestamp_iso.replace(":", "-").replace(".", "-")
    return f"{safe}.json"


def _new_synthesis_id() -> str:
    """Return a unique id for a synthesis.

    Format: ``<iso-ts-safe>-<6char>`` where the suffix is a short
    random hex tag. ISO timestamps are second-resolution; two
    syntheses in the same second would otherwise overwrite each other
    on disk. The 6-char suffix gives 16M values per second — collision
    risk is negligible and the suffix is human-readable as a "tag"
    when the dashboard shows multiple syntheses from the same minute.
    """
    import secrets

    from .schema import _utcnow_iso

    ts = _utcnow_iso().replace(":", "-").replace(".", "-")
    return f"{ts}-{secrets.token_hex(3)}"


def save_synthesis(
    root: Path,
    slug: str,
    question: str,
    memo: str,
    dossier: Optional[dict] = None,
    confidence_overall: Optional[float] = None,
    scope: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict:
    """Persist a synthesis as an append-only JSON file.

    Returns ``{slug, timestamp, path, evidence_count, contradiction_count,
    open_question_count, follow_up_count, synthesis_id}``. The
    ``synthesis_id`` is the timestamp string with ``:`` and ``.`` replaced
    so it is a valid id for cross-referencing in timelines.
    """
    from .schema import _utcnow_iso

    if not project_exists(root, slug):
        raise ProjectNotFoundError(f"no project with slug {slug!r}")

    timestamp = _utcnow_iso()
    safe_ts = _new_synthesis_id()
    d = _syntheses_dir(root, slug)
    d.mkdir(parents=True, exist_ok=True)

    payload = {
        "slug": slug,
        "synthesis_id": safe_ts,
        "timestamp": timestamp,
        "question": question,
        "scope": scope or "",
        "confidence_overall": confidence_overall,
        "memo": memo,
        "dossier": dossier or {},
        "session_id": session_id,
    }
    path = d / f"{safe_ts}.json"
    _atomic_write_json(path, payload)

    # Update project's last_active and append a timeline pointer so the
    # dashboard's "last touched" stat is accurate.
    try:
        project = load_project(root, slug)
        project.last_active = timestamp
        if session_id:
            project.last_session = session_id
        from .schema import TimelineEvent
        project.timeline.append(
            TimelineEvent(
                event=f"synthesis: {question[:80]}{'…' if len(question) > 80 else ''}"
            )
        )
        save_project(root, project)
    except Exception:
        # Don't fail synthesis save on bookkeeping failure.
        pass

    dossier = dossier or {}
    return {
        "slug": slug,
        "synthesis_id": safe_ts,
        "timestamp": timestamp,
        "path": str(path),
        "evidence_count": len(dossier.get("evidence_ranked", [])),
        "contradiction_count": len(dossier.get("contradictions", [])),
        "open_question_count": len(dossier.get("open_questions", [])),
        "follow_up_count": len(dossier.get("follow_up_suggestions", [])),
    }


def list_syntheses(
    root: Path, slug: str, limit: int = 20
) -> list[dict]:
    """Return metadata for the most-recent syntheses on a project.

    Newest first. Loads the memo body only when needed — the dashboard
    panel just needs the question + timestamp.
    """
    d = _syntheses_dir(root, slug)
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), reverse=True)
    out: list[dict] = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dossier = data.get("dossier") or {}
        out.append(
            {
                "synthesis_id": data.get("synthesis_id", f.stem),
                "timestamp": data.get("timestamp", ""),
                "question": data.get("question", ""),
                "scope": data.get("scope", ""),
                "confidence_overall": data.get("confidence_overall"),
                "evidence_count": len(dossier.get("evidence_ranked", [])),
                "contradiction_count": len(dossier.get("contradictions", [])),
                "open_question_count": len(dossier.get("open_questions", [])),
                "follow_up_count": len(dossier.get("follow_up_suggestions", [])),
                "memo_preview": (data.get("memo") or "")[:200],
            }
        )
    return out


def load_synthesis(root: Path, slug: str, synthesis_id: str) -> dict:
    """Load the full synthesis (memo + dossier) by id."""
    d = _syntheses_dir(root, slug)
    p = d / f"{synthesis_id}.json"
    if not p.exists():
        raise FileNotFoundError(
            f"no synthesis {synthesis_id!r} for project {slug!r}"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def list_all_syntheses(root: Path, limit: int = 30) -> list[dict]:
    """Return recent syntheses across every project (newest first)."""
    if not root.exists():
        return []
    out: list[dict] = []
    for proj_dir in root.iterdir():
        if not proj_dir.is_dir():
            continue
        syns = list_syntheses(root, proj_dir.name, limit=limit)
        for s in syns:
            s["project_slug"] = proj_dir.name
            out.append(s)
    out.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    return out[:limit]


# --- Manual override (dot-path field edits with Pydantic validation) ---------
#
# Lifted from ``__main__.rp_manual_override`` so the dashboard REST endpoint
# can apply overrides directly without spawning an MCP subprocess.  Validates
# the new value against the field's Pydantic annotation before writing, and
# logs a ``kind="manual"`` TimelineEvent with the reason.

from typing import Any  # noqa: E402

from .schema import ResearchProject, TimelineEvent, _utcnow_iso  # noqa: E402


def _coerce_override_value(value: Any, field_type: Any) -> Any:
    """Best-effort coercion of ``value`` to ``field_type`` for override writes.

    Mirrors ``__main__._coerce_override``.  Unwraps ``Optional[X]`` →
    ``X``, handles bool/int/float/str/list, then defers to Pydantic's
    TypeAdapter for richer types.  Returns ``value`` unchanged when
    ``field_type`` is None.
    """
    if field_type is None:
        return value

    origin = getattr(field_type, "__origin__", None)
    if origin is None and hasattr(field_type, "__args__"):
        args = field_type.__args__
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            field_type = non_none[0]
            origin = getattr(field_type, "__origin__", None)

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

    if field_type in (int, float, str):
        try:
            return field_type(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"cannot coerce {value!r} to {field_type.__name__}: {exc}"
            ) from exc

    if origin is list:
        if not isinstance(value, list):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return list(value)

    try:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(field_type)
        return adapter.validate_python(value)
    except Exception:
        return value


def _summarize_value(v: Any) -> str:
    """Single-line summary for the timeline."""
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


class FieldPathError(ValueError):
    """Raised when a manual_override field_path can't be resolved."""


def manual_override(
    root: Path,
    slug: str,
    field_path: str,
    new_value: Any,
    reason: str,
) -> dict:
    """Apply a typed manual override to any project field by dot-path.

    Returns a dict ``{project, field_path, old_value, new_value}`` on success.
    Raises ``ProjectNotFoundError`` if ``slug`` doesn't exist, ``FieldPathError``
    if the path can't be resolved, or ``ValueError`` if the new value fails
    type coercion.  The override is logged as ``kind="manual"`` in the
    timeline with the supplied ``reason``.
    """
    from pydantic import BaseModel as _BaseModel

    project = load_project(root, slug)

    segments = [s for s in field_path.split(".") if s]
    if not segments:
        raise FieldPathError("field_path must be non-empty")

    container: Any = project
    list_index: int | None = None

    for i, seg in enumerate(segments[:-1]):
        if isinstance(container, ResearchProject):
            attr = getattr(container, seg, None)
            if attr is None and not hasattr(container, seg):
                raise FieldPathError(f"unknown field {seg!r} on project")
            container = attr
            list_index = None
            continue
        if isinstance(container, list):
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
                raise FieldPathError(f"no list item {seg!r} at path position {i}")
            list_index = matched
            container = container[matched]
            continue
        raise FieldPathError(
            f"cannot navigate into {type(container).__name__} at segment {seg!r}"
        )

    final = segments[-1]
    old_value: Any = None
    applied: Any = None

    if isinstance(container, ResearchProject):
        if not hasattr(container, final):
            raise FieldPathError(f"unknown field {final!r} on project")
        old_value = getattr(container, final)
        annotation = ResearchProject.model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        applied = _coerce_override_value(new_value, field_type)
        setattr(container, final, applied)
    elif isinstance(container, list):
        matched = list_index if list_index is not None else None
        if matched is None:
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
            raise FieldPathError(f"cannot resolve list item {final!r}")
        item = container[matched]
        if not hasattr(item, final):
            raise FieldPathError(f"unknown field {final!r} on {type(item).__name__}")
        old_value = getattr(item, final)
        annotation = type(item).model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        applied = _coerce_override_value(new_value, field_type)
        setattr(item, final, applied)
    elif isinstance(container, _BaseModel):
        # Loop ended on a nested Pydantic item (e.g. Hypothesis reached via
        # list[id] or list[int-index]). Treat it like the project root but
        # use the item's own model_fields.
        if not hasattr(container, final):
            raise FieldPathError(
                f"unknown field {final!r} on {type(container).__name__}"
            )
        old_value = getattr(container, final)
        annotation = type(container).model_fields.get(final)
        field_type = annotation.annotation if annotation else None
        applied = _coerce_override_value(new_value, field_type)
        setattr(container, final, applied)
    else:
        raise FieldPathError(
            f"unsupported container type {type(container).__name__}"
        )

    project.timeline.append(
        TimelineEvent(
            event=(
                f"manual override {field_path}: {_summarize_value(old_value)} "
                f"→ {_summarize_value(applied)} (reason: {reason})"
            ),
            kind="manual",
        )
    )
    project.last_active = _utcnow_iso()
    save_project(root, project)

    return {
        "project": project.model_dump(mode="json"),
        "field_path": field_path,
        "old_value": old_value,
        "new_value": applied,
    }


__all__ = [
    "DEFAULT_STORAGE_ROOT",
    "ProjectNotFoundError",
    "ProjectAlreadyExistsError",
    "FieldPathError",
    "list_projects",
    "project_exists",
    "load_project",
    "save_project",
    "create_project",
    "delete_project",
    "archive_project",
    "render_report",
    "manual_override",
    "next_id",
]