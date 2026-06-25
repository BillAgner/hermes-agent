# tasks-mcp

Personal task & follow-up tracking for Hermes via MCP. SQLite-backed,
dashboard-friendly, cross-references research projects.

## Tools

- `tasks_health` — DB path, counts by status
- `tasks_add` — create a task with optional due date, priority, project link
- `tasks_list` — list tasks filtered by status / project / due date
- `tasks_get` — fetch one task
- `tasks_done` — mark task complete
- `tasks_cancel` — cancel a task (not done, just abandoned)
- `tasks_snooze` — push due date out
- `tasks_due_soon` — tasks due in the next N hours (for dashboard panel)
- `tasks_overdue` — overdue tasks (for dashboard panel)
- `tasks_remind` — mark reminder sent (called by poller; idempotent)

## Storage

`C:\Data\Hermes\personal\tasks.db` (env-overridable via `TASKS_DB`).
