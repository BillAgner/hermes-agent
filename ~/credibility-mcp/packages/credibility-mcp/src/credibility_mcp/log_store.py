"""Persistent log of research artifacts and their scored sources.

Each artifact is a JSON file under ``$CREDIBILITY_LOG_DIR/``. The directory
defaults to ``C:\\Data\\Hermes\\cache\\credibility_log\\`` and can be
overridden via the ``CREDIBILITY_LOG_DIR`` environment variable.

Files are append-only with a unique artifact_id filename so multiple
research streams don't collide. The dashboard reads these files to populate
the 'sources used' panel.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_log_dir() -> Path:
    base = os.environ.get("CREDIBILITY_LOG_DIR")
    if base:
        return Path(base)
    # Default to Hermes cache directory on Windows
    return Path("C:/Data/Hermes/cache/credibility_log")


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_artifact(
    artifact: dict[str, Any],
    *,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist a research artifact to disk and return the saved record.

    Expected input keys:
      - research_id (str): caller-chosen id; auto-generated if missing
      - title (str): human title
      - claims (list[dict]): each claim has text + composite_score + scored_sources
      - sources (list[dict]): flat list of all sources used
      - notes (str, optional)
    """
    base = _ensure_dir(log_dir or _default_log_dir())
    rid = artifact.get("research_id") or f"art-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    record = dict(artifact)
    record["research_id"] = rid
    record.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    out_path = base / f"{rid}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {"path": str(out_path), "research_id": rid, "bytes": out_path.stat().st_size}


def list_artifacts(
    *,
    log_dir: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List logged research artifacts (metadata only, no full sources)."""
    base = _ensure_dir(log_dir or _default_log_dir())
    out: list[dict[str, Any]] = []
    files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        sources = data.get("sources", [])
        scores = [s.get("score", 0.0) for s in sources if isinstance(s, dict)]
        avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
        out.append({
            "research_id": data.get("research_id"),
            "title": data.get("title"),
            "logged_at": data.get("logged_at"),
            "source_count": len(sources),
            "claim_count": len(data.get("claims", [])),
            "average_score": avg_score,
        })
    return out


def get_artifact(
    research_id: str,
    *,
    log_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Read a full artifact by research_id. Returns None if not found."""
    base = _ensure_dir(log_dir or _default_log_dir())
    target = base / f"{research_id}.json"
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))
