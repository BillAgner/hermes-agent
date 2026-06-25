# research-project-mcp

MCP server for structured research-project state — hypothesis tracking,
evidence accumulation, contradictions, and dead-ends across sessions.

Mirrors each project to [open-notebook](https://github.com/lfnovo/open-notebook)
so source URLs can be browsed in the UI.

## Tools

17 tools, all prefixed `rp_`:

- **Discovery**: `rp_list_projects`, `rp_get_project`, `rp_health`
- **Create / update**: `rp_create_project`, `rp_update_hypothesis`,
  `rp_add_hypothesis`, `rp_open_question`, `rp_answer_question`,
  `rp_mark_dead_end`, `rp_add_contradiction`
- **Evidence**: `rp_add_evidence` (mirrors URLs to open-notebook)
- **Read / report**: `rp_query_project`, `rp_sync_into_context`,
  `rp_render_report`
- **Meta**: `rp_archive_project`, `rp_manual_override`, `rp_link_session`

## Storage

Canonical state lives at `C:\Data\Hermes\research_projects\<slug>\state.json`
(overridable via `RESEARCH_PROJECTS_DIR`).