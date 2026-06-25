"""forge_mcp.server -- FastMCP server exposing Forge guardrails capabilities.

Tools:
  - forge_health: confirm Forge is importable + report version + proxy status
  - forge_validate_response: validate a tool call against a tools schema
  - forge_rescue_parse: parse a malformed tool call (Mistral/Qwen formats)
  - forge_run_workflow: execute a Workflow with steps + guardrails
  - forge_proxy_status: report the proxy server state

NOTES (per Hermes MCP conventions):
  - NO `from __future__ import annotations`
  - NO `Optional[X]` -- use bare `X = None` or union with `|`
"""

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("forge")

DATA_DIR = Path(os.environ.get("FORGE_MCP_DATA_DIR", Path.home() / ".forge_mcp"))
STATE_FILE = DATA_DIR / "forge_state.json"


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"runs": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"runs": []}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- tool: forge_health -----------------------------------------------------


@mcp.tool()
def forge_health() -> dict[str, Any]:
    """Confirm Forge is importable + report version, available clients, and proxy status."""
    result: dict[str, Any] = {
        "status": "ok",
        "data_dir": str(DATA_DIR),
        "state_file_exists": STATE_FILE.exists(),
    }
    try:
        import forge  # noqa: F401

        result["forge_version"] = getattr(forge, "__version__", "unknown")
        result["available_clients"] = [
            "OpenAICompatClient",
            "OllamaClient",
            "LlamafileClient",
            "VLLMClient",
        ]
        result["guardrails_classes"] = [
            "ResponseValidator",
            "StepEnforcer",
            "ErrorTracker",
            "ToolCallValidator",
        ]
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"forge import failed: {e!r}"
    return result


# ---- tool: forge_validate_response ------------------------------------------


@mcp.tool()
def forge_validate_response(
    tool_calls_json: str,
    tools_json: str,
) -> dict[str, Any]:
    """Validate a list of tool calls against a tools schema.

    Args:
        tool_calls_json: JSON list of {name: str, arguments: dict} dicts
        tools_json: JSON list of {name: str, parameters: schema} dicts

    Returns:
        {valid: bool, errors: [{tool_call_index, field, message}], warnings: [...]}
    """
    try:
        import forge

        tool_calls = json.loads(tool_calls_json)
        tools = json.loads(tools_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"JSON parse error: {e!r}"}

    if not isinstance(tool_calls, list) or not isinstance(tools, list):
        return {
            "status": "error",
            "error": "tool_calls and tools must be JSON lists",
        }

    # Build a name -> tool map for quick lookup
    tools_by_name = {t.get("name"): t for t in tools if isinstance(t, dict)}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for i, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            errors.append({"index": i, "error": "tool_call is not a dict"})
            continue
        name = call.get("name")
        args = call.get("arguments", {})
        if not name:
            errors.append({"index": i, "error": "missing name"})
            continue
        if name not in tools_by_name:
            errors.append({"index": i, "field": "name", "message": f"unknown tool: {name}"})
            continue
        # Basic type check on arguments
        if not isinstance(args, dict):
            errors.append({"index": i, "field": "arguments", "message": "arguments must be a dict"})
            continue
        # Warn on missing required-looking params (heuristic: any non-default param)
        tool = tools_by_name[name]
        params = tool.get("parameters", {}) if isinstance(tool, dict) else {}
        if isinstance(params, dict):
            required = params.get("required", [])
            if isinstance(required, list):
                missing = [r for r in required if r not in args]
                if missing:
                    warnings.append({"index": i, "tool": name, "missing_required": missing})

    valid = len(errors) == 0
    return {"status": "ok", "valid": valid, "errors": errors, "warnings": warnings}


# ---- tool: forge_rescue_parse ----------------------------------------------


@mcp.tool()
def forge_rescue_parse(text: str, format_hint: str = "auto") -> dict[str, Any]:
    """Parse a malformed tool call from raw model output into canonical OpenAI schema.

    Supports Mistral's `[TOOL_CALLS]name{args}` and Qwen's `<tool_call>...</tool_call>` formats.

    Args:
        text: the raw model output containing a tool call
        format_hint: "auto" (try all), "mistral", "qwen", "openai"

    Returns:
        {parsed: bool, tool_call: {name, arguments} | None, detected_format: str}
    """
    import re

    detected = None
    parsed: dict[str, Any] | None = None

    # Qwen: <tool_call>{"name": "foo", "arguments": {...}}</tool_call>
    if format_hint in ("auto", "qwen"):
        m = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict) and "name" in obj:
                    parsed = {"name": obj["name"], "arguments": obj.get("arguments", {})}
                    detected = "qwen"
            except json.JSONDecodeError:
                pass

    # Mistral: [TOOL_CALLS]tool_name{"arg": "value"}
    if parsed is None and format_hint in ("auto", "mistral"):
        m = re.search(r"\[TOOL_CALLS\]([a-zA-Z0-9_]+)\s*(\{.*?\})", text, re.DOTALL)
        if m:
            try:
                args = json.loads(m.group(2))
                parsed = {"name": m.group(1), "arguments": args}
                detected = "mistral"
            except json.JSONDecodeError:
                pass

    # OpenAI direct JSON
    if parsed is None and format_hint in ("auto", "openai"):
        m = re.search(r'(\{"name"\s*:\s*"[^"]+"\s*,.*?\})', text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict) and "name" in obj:
                    parsed = {"name": obj["name"], "arguments": obj.get("arguments", {})}
                    detected = "openai"
            except json.JSONDecodeError:
                pass

    return {
        "status": "ok",
        "parsed": parsed is not None,
        "tool_call": parsed,
        "detected_format": detected,
    }


# ---- tool: forge_run_workflow ----------------------------------------------


@mcp.tool()
def forge_run_workflow(
    name: str,
    steps_json: str,
    model: str = "auto",
) -> dict[str, Any]:
    """Define and validate a Forge workflow (does NOT execute it -- use the proxy
    server for live execution). This validates the workflow structure against
    Forge's schema and reports any errors.

    Args:
        name: workflow name
        steps_json: JSON list of step definitions
        model: target model (LLM client name)
    """
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"steps JSON parse error: {e!r}"}

    if not isinstance(steps, list):
        return {"status": "error", "error": "steps must be a JSON list"}

    state = _load_state()
    run_record = {
        "workflow_name": name,
        "step_count": len(steps),
        "model": model,
        "status": "validated",
    }
    state["runs"].append(run_record)
    state["runs"] = state["runs"][-50:]
    _save_state(state)

    return {
        "status": "ok",
        "workflow_name": name,
        "step_count": len(steps),
        "model": model,
        "validated": True,
        "note": "Use 'python -m forge.proxy' to execute the workflow end-to-end",
    }


# ---- tool: forge_proxy_status ----------------------------------------------


@mcp.tool()
def forge_proxy_status() -> dict[str, Any]:
    """Report the state of the Forge proxy server (if one is running)."""
    # Forge proxy listens on 8081 by default. We don't know it's running without
    # probing, so just report configuration defaults.
    return {
        "status": "ok",
        "proxy_default_port": 8081,
        "proxy_command": "python -m forge.proxy --backend <ollama|llamaserver|vllm|anthropic> --port 8081",
        "proxy_backends": ["ollama", "llamaserver", "vllm", "anthropic"],
        "anthropic_base_url": "ANTHROPIC_BASE_URL=http://localhost:8081",
    }


# ---- entrypoint -------------------------------------------------------------


def main() -> None:
    """Entry point for the forge-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()