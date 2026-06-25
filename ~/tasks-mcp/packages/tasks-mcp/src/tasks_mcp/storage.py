"""SQLite storage layer for tasks-mcp.

Single ``tasks`` table. Status is one of: open, done, cancelled. ``due_at``
is ISO 8601 UTC. ``priority`` is a 1-5 integer (1 = highest, 5 = lowest;
matches most todo conventions). ``project_slug`` is OPTIONAL — when set,
links the task to a research_project_mcp project so the dashboard can
group them.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path(
    os.environ.get("TASKS_DB")
    or r"C:\Data\Hermes\personal\tasks.db"
)


VALID_STATUSES = ("open", "done", "cancelled")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'done', 'cancelled')),
    priority        INTEGER NOT NULL DEFAULT 3
                    CHECK (priority BETWEEN 1 AND 5),
    due_at          TEXT,
    reminded_at     TEXT,
    reminded_count  INTEGER NOT NULL DEFAULT 0,
    project_slug    TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_slug);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def add_task(
    db_path: Path,
    title: str,
    description: str = "",
    priority: int = 3,
    due_at: str | None = None,
    project_slug: str | None = None,
    tags: list[str] | None = None,
    now_iso: str = "",
) -> dict:
    if not title or not title.strip():
        raise ValueError("title is required")
    if priority < 1 or priority > 5:
        raise ValueError(f"priority must be 1..5, got {priority}")
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    init_db(db_path)
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO tasks
                (title, description, status, priority, due_at,
                 project_slug, tags, created_at, updated_at)
            VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(), description, int(priority), due_at,
                project_slug,
                json.dumps(tags or [], ensure_ascii=False),
                now_iso, now_iso,
            ),
        )
        con.commit()
        return get_task(db_path, cur.lastrowid)
    finally:
        con.close()


def get_task(db_path: Path, task_id: int) -> dict:
    con = _connect(db_path)
    try:
        cur = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        return _row_to_task(row)
    finally:
        con.close()


def list_tasks(
    db_path: Path,
    status: Optional[str] = None,
    project_slug: Optional[str] = None,
    due_before: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    clauses = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if project_slug:
        clauses.append("project_slug = ?")
        params.append(project_slug)
    if due_before:
        clauses.append("due_at IS NOT NULL AND due_at <= ?")
        params.append(due_before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"SELECT * FROM tasks {where} ORDER BY "
            f"  CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, "
            f"  CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, "
            f"  due_at ASC, priority ASC, id ASC "
            f"LIMIT ?",
            params,
        )
        return [_row_to_task(r) for r in cur.fetchall()]
    finally:
        con.close()


def tasks_due_soon(db_path: Path, hours: int = 24) -> list[dict]:
    """Open tasks due within the next ``hours`` hours, sorted by due_at."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    until = (now + timedelta(hours=hours)).isoformat()
    return list_tasks(db_path, status="open", due_before=until, limit=200)


def tasks_overdue(db_path: Path) -> list[dict]:
    """Open tasks whose due_at is in the past."""
    from datetime import datetime, timezone
    return list_tasks(
        db_path, status="open",
        due_before=datetime.now(timezone.utc).isoformat(),
        limit=200,
    )


def mark_done(db_path: Path, task_id: int, now_iso: str = "") -> dict:
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (now_iso, now_iso, task_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise TaskNotFoundError(
                f"task {task_id} not found or already not-open"
            )
        return get_task(db_path, task_id)
    finally:
        con.close()


def cancel_task(db_path: Path, task_id: int, now_iso: str = "") -> dict:
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            UPDATE tasks SET status = 'cancelled', updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (now_iso, task_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise TaskNotFoundError(
                f"task {task_id} not found or already not-open"
            )
        return get_task(db_path, task_id)
    finally:
        con.close()


def snooze(db_path: Path, task_id: int, new_due_at: str, now_iso: str = "") -> dict:
    if not new_due_at:
        raise ValueError("new_due_at is required")
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            UPDATE tasks SET due_at = ?, reminded_at = NULL, reminded_count = 0, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (new_due_at, now_iso, task_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise TaskNotFoundError(
                f"task {task_id} not found or already not-open"
            )
        return get_task(db_path, task_id)
    finally:
        con.close()


def mark_reminded(db_path: Path, task_id: int, now_iso: str = "") -> dict:
    """Mark a reminder as sent for a task (idempotent — increments count)."""
    if not now_iso:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
    con = _connect(db_path)
    try:
        con.execute(
            """
            UPDATE tasks SET reminded_at = ?, reminded_count = reminded_count + 1,
                             updated_at = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, task_id),
        )
        con.commit()
        return get_task(db_path, task_id)
    finally:
        con.close()


def summary(db_path: Path) -> dict:
    init_db(db_path)
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["c"] for r in rows}
        overdue = tasks_overdue(db_path)
        due_24 = tasks_due_soon(db_path, hours=24)
        size = db_path.stat().st_size if db_path.exists() else 0
        return {
            "db_path": str(db_path),
            "size_bytes": size,
            "open_count": by_status.get("open", 0),
            "done_count": by_status.get("done", 0),
            "cancelled_count": by_status.get("cancelled", 0),
            "overdue_count": len(overdue),
            "due_24h_count": len(due_24),
        }
    finally:
        con.close()


class TaskNotFoundError(LookupError):
    pass


def _row_to_task(row: sqlite3.Row) -> dict:
    try:
        tags = json.loads(row["tags"]) if row["tags"] else []
    except (TypeError, ValueError):
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "priority": row["priority"],
        "due_at": row["due_at"],
        "reminded_at": row["reminded_at"],
        "reminded_count": row["reminded_count"],
        "project_slug": row["project_slug"],
        "tags": tags,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }
