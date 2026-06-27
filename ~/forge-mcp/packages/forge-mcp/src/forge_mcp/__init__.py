"""forge-mcp: MCP server exposing the forge guardrails library to Hermes.

Nine tools, all namespaced ``forge_*``::

    forge_health                  — server status, backend check, version
    forge_save_workflow           — persist a workflow definition (system prompt + tools schema)
    forge_list_workflows          — list saved workflows
    forge_get_workflow            — fetch a saved workflow's full definition
    forge_delete_workflow         — remove a saved workflow
    forge_run_workflow            — execute a saved workflow against a user message
    forge_run_inline              — execute a one-shot workflow without saving
    forge_rescue_tool_calls       — extract structured tool calls from non-canonical text
    forge_get_sampling_defaults   — look up card-recommended sampling for a model

Workflows are persisted as JSON in ``~/.hermes/forge/workflows/<name>.json`` so
they're editable, version-controllable, and survive across sessions.

Configuration is via env vars on the MCP subprocess (set via
``hermes mcp add --env KEY=VAL``):
    FORGE_BACKEND         — "ollama" | "llamafile" | "openai-compat"  (default: ollama)
    FORGE_BASE_URL        — backend root URL  (default per backend)
    FORGE_DEFAULT_MODEL   — default model name to use when caller omits one
    FORGE_WORKFLOWS_DIR   — override the workflow storage directory
    FORGE_TOOL_MODULES    — colon-separated module paths to import for callable tools

NOTE: Like the time-series-mcp sibling, do NOT add ``from __future__ import
annotations`` here — it makes annotations into strings and breaks FastMCP's
tool-decorator typing.
"""

from forge_mcp.__about__ import __version__

__all__ = ["__version__"]