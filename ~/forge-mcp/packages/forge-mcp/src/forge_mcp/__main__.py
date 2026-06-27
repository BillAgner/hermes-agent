"""forge-mcp FastMCP server.

Nine tools, all namespaced ``forge_*``::

    forge_health                  — server status, backend check, version
    forge_save_workflow           — persist a workflow definition
    forge_list_workflows          — list saved workflows
    forge_get_workflow            — fetch a saved workflow's full definition
    forge_delete_workflow         — remove a saved workflow
    forge_run_workflow            — execute a saved workflow against a user message
    forge_run_inline              — execute a one-shot workflow without saving
    forge_rescue_tool_calls       — extract structured tool calls from non-canonical text
    forge_get_sampling_defaults   — look up card-recommended sampling for a model

NOTE: Do NOT add ``from __future__ import annotations`` to this file —
FastMCP's tool-decorator typing breaks when annotations are strings.
"""

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from forge import rescue_tool_call

from forge_mcp.__about__ import __version__
from forge_mcp.client import get_config, health_check, load_tool_modules
from forge_mcp.runner import run_workflow
from forge_mcp.workflows import delete as workflow_delete
from forge_mcp.workflows import get as workflow_get
from forge_mcp.workflows import list_all as workflow_list_all
from forge_mcp.workflows import save as workflow_save
from forge_mcp.workflows import WorkflowValidationError


mcp = FastMCP("forge")

# User-supplied callable tools, loaded once at import time from FORGE_TOOL_MODULES.
# Exposed to workflow runners so any tool name defined here gets a real callable
# rather than the schema-only placeholder.
_EXTRA_CALLABLES: dict[str, Any] = load_tool_modules()


# ---- helpers --------------------------------------------------------------

def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _error(msg: str) -> str:
    return _to_json({"success": False, "error": msg})


def _ok(payload: Any) -> str:
    if isinstance(payload, dict) and "success" not in payload:
        payload = {"success": True, **payload}
    return _to_json(payload)


# ---- tools ----------------------------------------------------------------

@mcp.tool()
async def forge_health() -> str:
    """Server + backend status, configuration, and version info.

    Probes the configured local LLM backend (Ollama / llama-server / etc.)
    and returns whether it is reachable plus the list of available models.
    """
    info = await health_check()
    info["version"] = __version__
    info["extra_callables_loaded"] = sorted(_EXTRA_CALLABLES.keys())
    return _to_json(info)


@mcp.tool()
def forge_save_workflow(
    name: str,
    description: str,
    system_prompt: str,
    terminal_tool: str,
    tools: list[dict] | None = None,
    required_steps: list[str] | None = None,
) -> str:
    """Save a workflow definition to disk for reuse.

    A workflow is: a system prompt + an optional set of tool schemas + the
    name of the "terminal" tool the model must call to deliver its final
    answer. For repeated structured tasks (extraction, classification,
    summarization), define the `respond` tool as terminal and a clear system
    prompt that tells the model how to format its answer.

    Args:
        name: short identifier (e.g. "extract-companies"); must be unique
        description: one-line summary of what this workflow does
        system_prompt: the system prompt template (can include {var} placeholders)
        terminal_tool: name of the tool the model calls to deliver final answer
            (usually "respond")
        tools: list of tool schemas. Each is {name, description, parameters}
            where parameters is a JSON Schema object.
        required_steps: optional list of tool names that must be called before
            the model can reach the terminal_tool (advanced — leave empty for
            simple extraction/classification tasks).

    Returns the saved workflow (with timestamps) on success.
    """
    try:
        definition = {
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "terminal_tool": terminal_tool,
            "tools": tools or [],
            "required_steps": required_steps or [],
        }
        saved = workflow_save(definition)
        return _ok({"workflow": saved})
    except WorkflowValidationError as exc:
        return _error(f"Invalid workflow: {exc}")


@mcp.tool()
def forge_list_workflows() -> str:
    """List all saved workflows (summaries only).

    Use `forge_get_workflow(name=...)` to fetch the full definition.
    """
    items = workflow_list_all()
    return _ok({"workflows": items, "count": len(items)})


@mcp.tool()
def forge_get_workflow(name: str) -> str:
    """Fetch the full definition of a saved workflow by name."""
    definition = workflow_get(name)
    if definition is None:
        return _error(f"No workflow named {name!r}")
    return _ok({"workflow": definition})


@mcp.tool()
def forge_delete_workflow(name: str) -> str:
    """Remove a saved workflow from disk."""
    removed = workflow_delete(name)
    if not removed:
        return _error(f"No workflow named {name!r}")
    return _ok({"deleted": name})


@mcp.tool()
async def forge_run_workflow(
    workflow_name: str,
    user_message: str,
    model: str | None = None,
    max_iterations: int = 10,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    recommended_sampling: bool = True,
) -> str:
    """Execute a saved workflow against a user message.

    Returns a structured result with: the model's final answer, the list of
    tool calls it made, the per-turn trace, token usage, and the model that
    was actually used.

    Args:
        workflow_name: name of a previously saved workflow
        user_message: the user input to feed the workflow
        model: override the default model (default: FORGE_DEFAULT_MODEL env var)
        max_iterations: cap on workflow turns (default 10)
        recommended_sampling: apply card-recommended sampling params (default true)
        temperature/top_p/top_k: per-call sampling overrides (None = use defaults)
    """
    definition = workflow_get(workflow_name)
    if definition is None:
        return _error(f"No workflow named {workflow_name!r}")
    result = await run_workflow(
        workflow_def=definition,
        user_message=user_message,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        recommended_sampling=recommended_sampling,
        extra_callables=_EXTRA_CALLABLES,
    )
    return _to_json(result)


@mcp.tool()
async def forge_run_inline(
    system_prompt: str,
    user_message: str,
    terminal_tool: str = "respond",
    description: str = "",
    tools: list[dict] | None = None,
    required_steps: list[str] | None = None,
    workflow_name: str | None = None,
    model: str | None = None,
    max_iterations: int = 10,
    recommended_sampling: bool = True,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
) -> str:
    """Execute a one-shot workflow without saving it.

    Same semantics as forge_run_workflow, but the workflow definition is
    passed inline rather than loaded from disk. Useful for ad-hoc tasks,
    quick experiments, or when the caller doesn't want to persist state.

    Args:
        system_prompt: the system prompt template
        user_message: the user input
        terminal_tool: name of the tool the model calls to deliver final answer
        description: optional one-line summary (only used if workflow_name is set)
        tools: optional list of tool schemas (each {name, description, parameters})
        required_steps: optional list of tool names that must be called first
        workflow_name: if provided, also save this workflow before running
        model: override the default model
        max_iterations: cap on workflow turns (default 10)
        recommended_sampling: apply card-recommended sampling (default true)
    """
    if not description:
        description = f"Inline workflow ({terminal_tool})"
    definition = {
        "name": workflow_name or "_inline_",
        "description": description,
        "system_prompt": system_prompt,
        "terminal_tool": terminal_tool,
        "tools": tools or [],
        "required_steps": required_steps or [],
    }
    if workflow_name:
        try:
            workflow_save(definition)
        except WorkflowValidationError as exc:
            return _error(f"Invalid inline workflow: {exc}")
    result = await run_workflow(
        workflow_def=definition,
        user_message=user_message,
        model=model,
        max_iterations=max_iterations,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        recommended_sampling=recommended_sampling,
        extra_callables=_EXTRA_CALLABLES,
    )
    return _to_json(result)


@mcp.tool()
def forge_rescue_tool_calls(
    raw_text: str,
    available_tools: list[str] | None = None,
) -> str:
    """Extract structured tool calls from non-canonical model output.

    Handles: JSON in code fences, Mistral's [TOOL_CALLS]name{args} format,
    Qwen's <tool_call>...</tool_call> XML, and embedded JSON in surrounding text.

    This is the same parser forge uses inside its guardrails layer — exposed
    standalone for debugging and observability.

    Args:
        raw_text: the model's raw text output
        available_tools: optional list of valid tool names to match against;
            if provided, only calls to those names are returned
    """
    available = available_tools or []
    try:
        # rescue_tool_call handles the widest range of formats (Mistral
        # [TOOL_CALLS], Qwen <tool_call>, fenced JSON, embedded JSON). It's
        # a superset of extract_tool_call.
        rescued = rescue_tool_call(raw_text, available)
        # Merge dedup by (tool, args)
        seen: set[tuple[str, str]] = set()
        merged: list[dict[str, Any]] = []
        for tc in rescued:
            key = (tc.tool, json.dumps(tc.args, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            merged.append({"name": tc.tool, "arguments": tc.args, "call_id": getattr(tc, "call_id", None)})
        return _ok({"tool_calls": merged, "count": len(merged)})
    except Exception as exc:
        return _error(f"rescue failed: {type(exc).__name__}: {exc}")


@mcp.tool()
def forge_get_sampling_defaults(model: str) -> str:
    """Look up card-recommended sampling parameters for a model.

    Smart-resolves Ollama-style bare names (``qwen3:8b``) to their
    quant-suffixed registry keys (``qwen3:8b-q4_K_M``) automatically.
    Returns an empty dict for models not in forge's registry — the caller
    can then choose to fall back to backend defaults.

    Pass these as kwargs to your client, or use the recommended_sampling=True
    flag on forge_run_workflow / forge_run_inline to apply them automatically.
    """
    from forge_mcp.client import resolve_sampling_defaults
    try:
        defaults = resolve_sampling_defaults(model)
    except Exception as exc:
        return _error(f"lookup failed: {type(exc).__name__}: {exc}")
    return _ok({
        "model": model,
        "sampling_defaults": defaults,
        "count": len(defaults),
    })


def main() -> None:
    """Console-script entry point: `forge-mcp`."""
    # Print a one-line startup banner to stderr so the user knows which
    # backend the MCP subprocess is configured for (visible during
    # `hermes mcp test forge` and in any MCP launch logs).
    cfg = get_config()
    print(
        f"[forge-mcp v{__version__}] backend={cfg['backend']} "
        f"base_url={cfg['base_url']} "
        f"default_model={cfg['default_model'] or '(unset)'} "
        f"workflows_dir={cfg['workflows_dir']} "
        f"extra_tools={len(_EXTRA_CALLABLES)}",
        flush=True,
    )
    mcp.run()


if __name__ == "__main__":
    main()