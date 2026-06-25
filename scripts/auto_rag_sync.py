"""auto_rag_sync.py — mirror research-project evidence to open-notebook.

For every active research project, ensures:
  1. A notebook exists in open-notebook (created if missing).
  2. Every evidence item with a source URL is mirrored to the notebook
     as a `source_link` with `embed=true` (so ``on_search`` finds it
     semantically).

State is tracked in ``health/auto_rag_state.json`` (idempotent — re-running
only mirrors new evidence). The on-disk state file lets the dashboard show
"last run X hours ago, N items mirrored" without polling the MCPs.

Usage:
    python scripts/auto_rag_sync.py            # run once, print summary
    python scripts/auto_rag_sync.py --dry      # report what would be done

Cron (nightly at 2am):
    0 2 * * *  python scripts/auto_rag_sync.py

Safety:
    * Never deletes or modifies existing open-notebook sources — only adds.
    * Refuses to mirror to archived projects (read-only pass).
    * Records every (project_slug, evidence_id) mirror attempt in state
      so duplicate work is skipped on subsequent runs.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
RESEARCH_ROOT = HERMES_HOME / "research_projects"
STATE_PATH = HERMES_HOME / "health" / "auto_rag_state.json"
LOG_PATH = HERMES_HOME / "health" / "auto_rag.log"
OPEN_NOTEBOOK_BASE = os.environ.get("OPEN_NOTEBOOK_BASE", "http://localhost:5055/api")


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_dt.datetime.now().isoformat()}  {msg}"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    # Truncate to last 100 entries
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > 100:
            LOG_PATH.write_text("\n".join(lines[-100:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "runs": [], "mirrored": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"state corrupt, starting fresh: {e}")
        return {"version": 1, "runs": [], "mirrored": {}}


def _save_state(s: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Keep only last 30 runs in history
    s["runs"] = s.get("runs", [])[-30:]
    STATE_PATH.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")


def _api(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Call open-notebook REST API. Returns parsed JSON or {error, _status}.

    Body is sent as application/json (works for /notebooks). For /sources
    we need multipart/form-data, so callers building source-link payloads
    should use ``_api_multipart`` instead.
    """
    url = f"{OPEN_NOTEBOOK_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        return {"error": f"HTTP {e.code}: {err_body[:200]}", "_status": e.code}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _api_multipart(path: str, fields: dict[str, str], timeout: float = 60.0) -> dict[str, Any]:
    """POST multipart/form-data to open-notebook (used for /sources)."""
    import io
    import uuid
    boundary = "----hermesRAG" + uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.write(str(v).encode("utf-8"))
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    url = f"{OPEN_NOTEBOOK_BASE}{path}"
    req = urllib.request.Request(url, data=body.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        return {"error": f"HTTP {e.code}: {err_body[:300]}", "_status": e.code}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _load_project_state(slug: str) -> dict[str, Any] | None:
    p = RESEARCH_ROOT / slug / "state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"failed to read {slug}/state.json: {e}")
        return None


def _list_active_projects() -> list[dict[str, Any]]:
    registry = RESEARCH_ROOT / "_registry.json"
    if not registry.exists():
        return []
    try:
        reg = json.loads(registry.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"failed to read _registry.json: {e}")
        return []
    out: list[dict[str, Any]] = []
    # Registry may be a list of {slug,...} OR a dict keyed by slug.
    items: list[dict[str, Any]]
    if isinstance(reg, list):
        items = reg
    elif isinstance(reg, dict):
        # Could be {"projects": [...]} OR {slug: {...}} directly.
        if "projects" in reg and isinstance(reg["projects"], list):
            items = reg["projects"]
        else:
            items = [{"slug": k, **(v or {})} for k, v in reg.items()]
    else:
        items = []
    for p in items:
        if p.get("status") in ("active", "paused"):
            slug = p.get("slug") or p.get("id")
            if slug:
                p.setdefault("slug", slug)
                out.append(p)
    return out


def _ensure_notebook(slug: str, title: str, description: str) -> str | None:
    """Find-or-create the notebook for this project slug. Returns notebook_id or None."""
    listing = _api("GET", "/notebooks")
    if isinstance(listing, list):
        for nb in listing:
            if nb.get("name") == title or nb.get("id", "").endswith(slug):
                return nb["id"]
    else:
        # Some open-notebook versions return {notebooks: [...]}
        items = listing.get("notebooks", []) if isinstance(listing, dict) else []
        for nb in items:
            if nb.get("name") == title:
                return nb["id"]

    # Create new notebook
    result = _api("POST", "/notebooks", {"name": title, "description": description[:500]})
    nb_id = result.get("id") if isinstance(result, dict) else None
    if nb_id:
        _log(f"created notebook {nb_id} for {slug}")
    else:
        _log(f"failed to create notebook for {slug}: {result}")
    return nb_id


def _mirror_evidence(notebook_id: str, evidence: dict[str, Any], embed: bool = True) -> dict[str, Any]:
    """Create a source_link in open-notebook for one evidence item.

    open-notebook's /sources endpoint expects multipart/form-data with a
    ``type`` field ('link' for URL sources). notebook_id attaches the
    source to that notebook in the same call. embed='true' triggers vector
    indexing so on_search finds the page semantically.
    """
    sources = [s for s in (evidence.get("sources") or []) if s.startswith(("http://", "https://"))]
    if not sources:
        return {"error": "no http sources", "skipped": True}
    title = f"[{evidence.get('id', '?')}] {evidence.get('claim', '?')[:120]}"
    if len(sources) > 1:
        title += f" (+{len(sources) - 1} more)"
    return _api_multipart("/sources", {
        "type": "link",
        "notebook_id": notebook_id,
        "url": sources[0],
        "title": title,
        "embed": "true" if embed else "false",
        "async_processing": "true",
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror research evidence to open-notebook")
    ap.add_argument("--dry", action="store_true", help="Report what would be done without writing")
    args = ap.parse_args()

    started = _dt.datetime.now(_dt.timezone.utc).isoformat()
    state = _load_state()
    mirrored = state.setdefault("mirrored", {})
    summary = {
        "started_at": started,
        "projects_scanned": 0,
        "notebooks_created": 0,
        "evidence_mirrored": 0,
        "evidence_skipped_already": 0,
        "evidence_skipped_no_url": 0,
        "errors": [],
    }

    projects = _list_active_projects()
    _log(f"scan start: {len(projects)} active project(s)")

    for p in projects:
        slug = p["slug"]
        proj = _load_project_state(slug)
        if not proj:
            summary["errors"].append(f"{slug}: state.json unreadable")
            continue
        summary["projects_scanned"] += 1

        # Step 1: ensure notebook exists
        nb_id = proj.get("notebook_id")
        if not nb_id:
            if args.dry:
                summary["notebooks_created"] += 1
                nb_id = f"DRY:{slug}"
            else:
                nb_id = _ensure_notebook(
                    slug,
                    title=f"research:{slug} — {proj.get('title', slug)[:60]}",
                    description=proj.get("scope", ""),
                )
                if nb_id:
                    summary["notebooks_created"] += 1
                    # Update project state to record the notebook id
                    proj["notebook_id"] = nb_id
                    (RESEARCH_ROOT / slug / "state.json").write_text(
                        json.dumps(proj, indent=2, default=str), encoding="utf-8"
                    )
                    # Also update the registry so /api/research/projects
                    # reports the mirror (the dashboard reads from registry,
                    # not state.json, for cheap lookups).
                    reg_path = RESEARCH_ROOT / "_registry.json"
                    if reg_path.exists():
                        try:
                            reg = json.loads(reg_path.read_text(encoding="utf-8"))
                            if slug in reg:
                                reg[slug]["notebook_id"] = nb_id
                                reg[slug]["last_active"] = _dt.datetime.now(
                                    _dt.timezone.utc
                                ).isoformat()
                                reg_path.write_text(
                                    json.dumps(reg, indent=2, default=str),
                                    encoding="utf-8",
                                )
                        except Exception as e:
                            _log(f"registry update failed for {slug}: {e}")
                else:
                    summary["errors"].append(f"{slug}: notebook creation failed")
                    continue

        # Step 2: mirror each evidence item
        proj_mirrored = mirrored.setdefault(slug, {})
        for ev in proj.get("evidence", []):
            ev_id = ev.get("id")
            if not ev_id:
                continue
            if proj_mirrored.get(ev_id):
                summary["evidence_skipped_already"] += 1
                continue
            sources = [s for s in (ev.get("sources") or []) if s.startswith(("http://", "https://"))]
            if not sources:
                summary["evidence_skipped_no_url"] += 1
                continue
            if args.dry:
                summary["evidence_mirrored"] += 1
                proj_mirrored[ev_id] = {"mirrored_at": started, "dry": True, "url": sources[0]}
                continue
            result = _mirror_evidence(nb_id, ev)
            if result.get("id"):
                summary["evidence_mirrored"] += 1
                proj_mirrored[ev_id] = {
                    "mirrored_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "notebook_id": nb_id,
                    "open_notebook_source_id": result["id"],
                    "url": sources[0],
                }
            elif result.get("skipped"):
                summary["evidence_skipped_no_url"] += 1
            else:
                summary["errors"].append(f"{slug}/{ev_id}: {result.get('error', 'unknown')}")

    # Persist state
    state["runs"].append(summary)
    if not args.dry:
        _save_state(state)
    _log(
        f"scan done: scanned={summary['projects_scanned']} "
        f"new_notebooks={summary['notebooks_created']} "
        f"mirrored={summary['evidence_mirrored']} "
        f"skipped_already={summary['evidence_skipped_already']} "
        f"skipped_no_url={summary['evidence_skipped_no_url']} "
        f"errors={len(summary['errors'])}"
    )

    if args.dry:
        print("[DRY-RUN] no changes written")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["errors"] else 0  # don't fail on errors; just report


if __name__ == "__main__":
    sys.exit(main())
