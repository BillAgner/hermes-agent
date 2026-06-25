"""tasks-mcp FastMCP server.

Ten tools, namespaced ``tasks_*``::

    tasks_health          — DB path, counts by status, overdue/due_24h
    tasks_add             — create a task (title required; priority/due/project/tags optional)
    tasks_list            — list with status / project / due_before filters
    tasks_get             — fetch one task by id
    tasks_done            — mark task complete (idempotent guard)
    tasks_cancel          — mark task cancelled (idempotent guard)
    tasks_snooze          — push due_at out, reset reminded_count
    tasks_due_soon        — open tasks due within N hours
    tasks_overdue         — open tasks past due
    tasks_remind          — mark reminder sent (called by the dashboard poller)

NOTE: Do NOT add ``from __future__ import annotations`` to this file —
it makes annotations into strings and breaks FastMCP's tool-decorator
typing. Annotations below use bare types (``X = None``, not ``Optional[X]``).
"""

import json
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from tasks_mcp import storage
from tasks_mcp.__about__ import __version__


mcp = FastMCP("tasks")


def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(msg: str) -> str:
    return _to_json({"success": False, "error": msg})


@mcp.tool()
def tasks_health() -> str:
    """DB path + counts by status + overdue + due-in-24h counts."""
    return _to_json({"success": True, "version": __version__, **storage.summary(storage.DEFAULT_DB_PATH)})


@mcp.tool()
def tasks_add(
    title: str,
    description: str = "",
    priority: int = 3,
    due_at: str = None,
    project_slug: str = None,
    tags: list = None,
) -> str:
    """Create a task.

    Args:
        title: One-line task title (required).
        description: Longer description / context.
        priority: 1 (highest) to 5 (lowest). Default 3.
        due_at: ISO 8601 datetime string. None = no deadline.
        project_slug: Optional link to a research_project_mcp project slug.
        tags: Optional list of tag strings.
    """
    try:
        t = storage.add_task(
            db_path=storage.DEFAULT_DB_PATH,
            title=title, description=description,
            priority=priority, due_at=due_at,
            project_slug=project_slug, tags=tags or [],
            now_iso=_now_iso(),
        )
        return _to_json({"success": True, "task": t})
    except (ValueError, storage.TaskNotFoundError) as e:
        return _error(str(e))


@mcp.tool()
def tasks_list(
    status: str = None,
    project_slug: str = None,
    due_before: str = None,
    limit: int = 100,
) -> str:
    """List tasks with optional filters.

    Args:
        status: One of: open, done, cancelled. None = all.
        project_slug: Filter to one research_project_mcp project.
        due_before: ISO 8601 cutoff. Only tasks with due_at <= cutoff are returned.
        limit: Max rows (default 100).
    """
    try:
        rows = storage.list_tasks(
            storage.DEFAULT_DB_PATH,
            status=status, project_slug=project_slug,
            due_before=due_before, limit=limit,
        )
        return _to_json({
            "success": True,
            "row_count": len(rows),
            "tasks": rows,
        })
    except Exception as e:
        return _error(str(e))


@mcp.tool()
def tasks_get(task_id: int) -> str:
    """Fetch one task by id."""
    try:
        return _to_json({"success": True, "task": storage.get_task(storage.DEFAULT_DB_PATH, task_id)})
    except storage.TaskNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def tasks_done(task_id: int) -> str:
    """Mark task complete."""
    try:
        t = storage.mark_done(storage.DEFAULT_DB_PATH, task_id, _now_iso())
        return _to_json({"success": True, "task": t})
    except storage.TaskNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def tasks_cancel(task_id: int) -> str:
    """Cancel a task (not done, just abandoned)."""
    try:
        t = storage.cancel_task(storage.DEFAULT_DB_PATH, task_id, _now_iso())
        return _to_json({"success": True, "task": t})
    except storage.TaskNotFoundError as e:
        return _error(str(e))


@mcp.tool()
def tasks_snooze(task_id: int, new_due_at: str) -> str:
    """Push due_at out and reset reminded_count."""
    try:
        t = storage.snooze(storage.DEFAULT_DB_PATH, task_id, new_due_at, _now_iso())
        return _to_json({"success": True, "task": t})
    except (ValueError, storage.TaskNotFoundError) as e:
        return _error(str(e))


@mcp.tool()
def tasks_due_soon(hours: int = 24) -> str:
    """Open tasks due within the next ``hours`` hours. Default 24."""
    rows = storage.tasks_due_soon(storage.DEFAULT_DB_PATH, hours)
    return _to_json({
        "success": True,
        "hours": hours,
        "row_count": len(rows),
        "tasks": rows,
    })


@mcp.tool()
def tasks_overdue() -> str:
    """Open tasks whose due_at is in the past."""
    rows = storage.tasks_overdue(storage.DEFAULT_DB_PATH)
    return _to_json({
        "success": True,
        "row_count": len(rows),
        "tasks": rows,
    })


@mcp.tool()
def tasks_remind(task_id: int) -> str:
    """Mark a reminder as sent. Idempotent — increments reminded_count."""
    try:
        t = storage.mark_reminded(storage.DEFAULT_DB_PATH, task_id, _now_iso())
        return _to_json({"success": True, "task": t})
    except storage.TaskNotFoundError as e:
        return _error(str(e))


def main() -> None:
    storage.init_db(storage.DEFAULT_DB_PATH)
    mcp.run()


if __name__ == "__main__":
    main()
