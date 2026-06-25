"""
router.py — routing table CRUD + validation log + maturity tracking.

Storage layout under $HERMES_HOME/routing/ (default ~/.hermes/routing/):
  - table.json          routing table (read/written atomically)
  - validation_log.jsonl  append-only log of every shadow-validation run

Table shape (version 1):
  {
    "version": 1,
    "updated_at": "<iso8601>",
    "tasks": {
      "<task_id>": {
        "name": "<human readable>",
        "kind": "auxiliary" | "user",
        "config_path": "<yaml path within config.yaml, e.g. 'auxiliary.compression'>",
        "signature": "<what identifies this task>",
        "created_at": "<iso8601>",
        "maturity": {
          "success_count": int,
          "first_seen": "<iso8601>",
          "last_success": "<iso8601>",
          "status": "new" | "mature" | "validating" | "promoted" | "demoted"
        },
        "validation": {
          "n_runs": int,
          "avg_similarity": float | null,
          "min_similarity": float | null,
          "max_similarity": float | null,
          "last_validated": "<iso8601>" | null,
          "history": [{"ts", "method", "similarity", "decision"}]  # last 20 only
        },
        "routing": {
          "current": "cloud" | "local",
          "promoted_at": "<iso8601>" | null,
          "promoted_by": "<manual|auto|seed>" | null,
          "rationale": str | null
        }
      }
    }
  }
"""
from __future__ import annotations

import json
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _config import get, get_int

HERMES_HOME = Path(get("HERMES_HOME")).expanduser()
ROUTING_DIR = HERMES_HOME / "routing"
TABLE_PATH = ROUTING_DIR / "table.json"
LOG_PATH = ROUTING_DIR / "validation_log.jsonl"

# Maturity threshold — number of successful runs before a task is considered
# mature enough to start shadow-validation.
DEFAULT_MATURITY_THRESHOLD = get_int("ROUTING_MATURITY_THRESHOLD", 5)

# Concurrency guard for multi-process writes (cron + interactive CLI).
_LOCK = threading.Lock()


# ---------- Time helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Atomic IO ----------

def _atomic_write(path: Path, content: str) -> None:
    """Write to a temp file in the same dir, then os.replace() for atomicity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------- Table CRUD ----------

def load_table() -> dict[str, Any]:
    with _LOCK:
        if not TABLE_PATH.exists():
            return {"version": 1, "updated_at": _now(), "tasks": {}}
        return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def save_table(table: dict[str, Any]) -> None:
    with _LOCK:
        table["updated_at"] = _now()
        _atomic_write(TABLE_PATH, json.dumps(table, indent=2, sort_keys=True))


def get_task(task_id: str) -> dict[str, Any] | None:
    return load_table().get("tasks", {}).get(task_id)


def upsert_task(task_id: str, **fields: Any) -> dict[str, Any]:
    """Insert or update a task. Returns the updated task entry."""
    table = load_table()
    task = table["tasks"].get(task_id, {})
    task.update(fields)
    # Ensure required structure
    task.setdefault("name", task_id)
    task.setdefault("kind", "user")
    task.setdefault("signature", task_id)
    task.setdefault("created_at", _now())
    task.setdefault(
        "maturity",
        {
            "success_count": 0,
            "first_seen": _now(),
            "last_success": None,
            "status": "new",
        },
    )
    task.setdefault(
        "validation",
        {
            "n_runs": 0,
            "avg_similarity": None,
            "min_similarity": None,
            "max_similarity": None,
            "last_validated": None,
            "history": [],
        },
    )
    task.setdefault(
        "routing",
        {"current": "cloud", "promoted_at": None, "promoted_by": None, "rationale": None},
    )
    table["tasks"][task_id] = task
    save_table(table)
    return task


def record_success(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Increment success count, update last_success, flip to 'mature' at threshold."""
    table = load_table()
    task = table["tasks"].get(task_id)
    if task is None:
        task = upsert_task(task_id)
    mat = task["maturity"]
    mat["success_count"] = mat.get("success_count", 0) + 1
    mat["last_success"] = _now()
    if mat["status"] in ("new",) and mat["success_count"] >= DEFAULT_MATURITY_THRESHOLD:
        mat["status"] = "mature"
    for k, v in kwargs.items():
        if k in mat:
            mat[k] = v
    save_table(table)
    return task


def record_validation(
    task_id: str,
    similarity: float,
    method: str,
    decision: str,
    **extra: Any,
) -> dict[str, Any]:
    """Append a validation result, update aggregate stats."""
    table = load_table()
    task = table["tasks"].get(task_id)
    if task is None:
        task = upsert_task(task_id)
    val = task["validation"]
    val["n_runs"] = val.get("n_runs", 0) + 1
    val["last_validated"] = _now()
    # Rolling stats
    prev_avg = val.get("avg_similarity")
    n = val["n_runs"]
    if prev_avg is None:
        val["avg_similarity"] = similarity
        val["min_similarity"] = similarity
        val["max_similarity"] = similarity
    else:
        val["avg_similarity"] = prev_avg + (similarity - prev_avg) / n
        val["min_similarity"] = min(val.get("min_similarity", similarity), similarity)
        val["max_similarity"] = max(val.get("max_similarity", similarity), similarity)
    history = val.setdefault("history", [])
    history.append(
        {
            "ts": val["last_validated"],
            "method": method,
            "similarity": round(similarity, 4),
            "decision": decision,
            **extra,
        }
    )
    # Keep only the last 20 entries to bound the file
    val["history"] = history[-20:]
    # Update maturity status based on decision
    if decision == "pass" and task["maturity"]["status"] in ("mature", "validating"):
        task["maturity"]["status"] = "validating"  # passed one run; keep validating
    save_table(table)
    # Also append to the append-only log
    log_validation(
        task_id=task_id,
        similarity=similarity,
        method=method,
        decision=decision,
        **extra,
    )
    return task


def promote(
    task_id: str,
    *,
    by: str = "manual",
    rationale: str | None = None,
) -> dict[str, Any]:
    """Mark a task as routed-to-local."""
    table = load_table()
    task = table["tasks"].get(task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    task["routing"]["current"] = "local"
    task["routing"]["promoted_at"] = _now()
    task["routing"]["promoted_by"] = by
    task["routing"]["rationale"] = rationale
    task["maturity"]["status"] = "promoted"
    save_table(table)
    return task


def demote(task_id: str, *, rationale: str | None = None) -> dict[str, Any]:
    """Send a task back to cloud (e.g., after a quality regression)."""
    table = load_table()
    task = table["tasks"].get(task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    task["routing"]["current"] = "cloud"
    task["routing"]["rationale"] = rationale or "manually demoted"
    task["maturity"]["status"] = "mature"  # still mature, just not promoted anymore
    save_table(table)
    return task


def reset(task_id: str) -> dict[str, Any]:
    """Clear validation history (keeps maturity/success count)."""
    table = load_table()
    task = table["tasks"].get(task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")
    task["validation"] = {
        "n_runs": 0,
        "avg_similarity": None,
        "min_similarity": None,
        "max_similarity": None,
        "last_validated": None,
        "history": [],
    }
    save_table(table)
    return task


# ---------- Append-only validation log ----------

def log_validation(
    task_id: str,
    *,
    similarity: float,
    method: str,
    decision: str,
    **extra: Any,
) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now(),
        "task_id": task_id,
        "similarity": round(similarity, 4),
        "method": method,
        "decision": decision,
        **extra,
    }
    with _LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")


def tail_log(n: int = 20) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------- Selectors ----------

def tasks_with_status(*statuses: str) -> list[tuple[str, dict[str, Any]]]:
    table = load_table()
    out = []
    for tid, task in table.get("tasks", {}).items():
        if task.get("maturity", {}).get("status") in statuses:
            out.append((tid, task))
    return out


def eligible_for_auto_promote(
    *,
    min_runs: int = 3,
    min_avg_similarity: float = 0.95,
    min_min_similarity: float = 0.90,
) -> list[tuple[str, dict[str, Any]]]:
    """Tasks that have passed enough validation to be safely auto-promoted."""
    out = []
    for tid, task in tasks_with_status("mature", "validating"):
        v = task.get("validation", {})
        n = v.get("n_runs", 0)
        avg = v.get("avg_similarity")
        mn = v.get("min_similarity")
        if n < min_runs:
            continue
        if avg is None or avg < min_avg_similarity:
            continue
        if mn is None or mn < min_min_similarity:
            continue
        if task.get("routing", {}).get("current") == "local":
            continue  # already promoted
        out.append((tid, task))
    return out
