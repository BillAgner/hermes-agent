"""Workflow storage — JSON files in ~/.hermes/forge/workflows/<name>.json.

Each workflow file looks like::

    {
        "name": "extract-companies",
        "description": "Pull company names and tickers out of a news blurb.",
        "system_prompt": "You are an entity extractor. Respond with a JSON list of {\"name\", \"ticker\"} objects via the respond tool.",
        "tools": [
            {
                "name": "respond",
                "description": "Submit your final structured answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "description": "..."}
                    },
                    "required": ["answer"]
                }
            }
        ],
        "required_steps": [],
        "terminal_tool": "respond",
        "created_at": "2026-06-25T14:30:00Z",
        "updated_at": "2026-06-25T14:30:00Z"
    }

The MCP server reads/writes these directly; users can hand-edit them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorkflowValidationError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


REQUIRED_FIELDS = {"name", "description", "system_prompt", "terminal_tool"}
VALID_TOOL_KEYS = {"name", "description", "parameters"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def workflows_dir() -> Path:
    """Lazy-resolve the workflows directory from the env-config singleton.

    Centralized here so callers don't import the client module just for this.
    """
    from forge_mcp.client import get_config
    p = Path(get_config()["workflows_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def _workflow_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise WorkflowValidationError(
            f"Invalid workflow name {name!r} — must be a single path segment"
        )
    return workflows_dir() / f"{name}.json"


def validate(definition: dict[str, Any]) -> None:
    """Raise WorkflowValidationError if the definition is malformed."""
    if not isinstance(definition, dict):
        raise WorkflowValidationError("Workflow definition must be a JSON object")
    missing = REQUIRED_FIELDS - set(definition.keys())
    if missing:
        raise WorkflowValidationError(
            f"Workflow missing required fields: {sorted(missing)}"
        )
    if not isinstance(definition.get("name"), str) or not definition["name"].strip():
        raise WorkflowValidationError("Workflow 'name' must be a non-empty string")
    tools = definition.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise WorkflowValidationError("Workflow 'tools' must be a list")
        for i, t in enumerate(tools):
            if not isinstance(t, dict):
                raise WorkflowValidationError(f"tools[{i}] must be an object")
            extra = set(t.keys()) - VALID_TOOL_KEYS
            if extra:
                raise WorkflowValidationError(
                    f"tools[{i}] has unknown fields: {sorted(extra)}"
                )
            if "name" not in t or "description" not in t or "parameters" not in t:
                raise WorkflowValidationError(
                    f"tools[{i}] missing required keys (name, description, parameters)"
                )
            if not isinstance(t["name"], str) or not t["name"].strip():
                raise WorkflowValidationError(f"tools[{i}].name must be a non-empty string")
            if not isinstance(t["description"], str):
                raise WorkflowValidationError(f"tools[{i}].description must be a string")
            if not isinstance(t["parameters"], dict):
                raise WorkflowValidationError(f"tools[{i}].parameters must be an object")
    required_steps = definition.get("required_steps", [])
    if not isinstance(required_steps, list):
        raise WorkflowValidationError("'required_steps' must be a list of tool names")
    terminal = definition.get("terminal_tool")
    if not isinstance(terminal, (str, list)) or (
        isinstance(terminal, str) and not terminal.strip()
    ):
        raise WorkflowValidationError("'terminal_tool' must be a non-empty string or list")


def save(definition: dict[str, Any]) -> dict[str, Any]:
    """Persist a workflow. Returns the saved (with timestamps) version."""
    validate(definition)
    name = definition["name"].strip()
    path = _workflow_path(name)
    now = _now_iso()
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        created_at = existing.get("created_at", now)
    else:
        created_at = now
    out = {**definition, "name": name, "created_at": created_at, "updated_at": now}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def get(name: str) -> dict[str, Any] | None:
    path = _workflow_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_all() -> list[dict[str, Any]]:
    """Return summaries for all saved workflows."""
    d = workflows_dir()
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "name": data.get("name", p.stem),
            "description": data.get("description", ""),
            "terminal_tool": data.get("terminal_tool"),
            "tool_count": len(data.get("tools", [])),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "path": str(p),
        })
    return out


def delete(name: str) -> bool:
    path = _workflow_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


def to_forge_workflow(definition: dict[str, Any], extra_callables: dict[str, Any] | None = None):
    """Convert a saved JSON workflow into a forge.Workflow object.

    Each tool in the JSON becomes a forge ToolDef. The ``callable`` slot for
    each tool is either:
      - looked up in ``extra_callables`` (for user-imported modules), or
      - if the name is ``respond``, forge's built-in ``respond_tool()`` is used
        (the model calls ``respond(message=...)`` to deliver its final answer), or
      - otherwise a synthetic placeholder that returns a hint to the model
        to call the terminal ``respond`` tool instead.

    For most "repeated structured task" use cases (extraction, classification,
    summarization), the workflow defines only the terminal ``respond`` tool and
    no callables are needed — the model produces its answer and forge routes
    it through the built-in handler.
    """
    from forge import RESPOND_TOOL_NAME, ToolDef, ToolSpec, Workflow, respond_tool

    tool_specs: dict[str, ToolDef] = {}
    tools_json = definition.get("tools") or []
    extra_callables = extra_callables or {}

    terminal = definition.get("terminal_tool")
    # Pre-build the built-in respond tool if it's the terminal — this lets users
    # set terminal_tool="respond" without redefining it in their JSON.
    respond_tooldef = respond_tool() if terminal == RESPOND_TOOL_NAME else None

    # If terminal is respond and it's not in the user's tools list, auto-inject it.
    user_tool_names = {t["name"] for t in tools_json}
    if respond_tooldef is not None and RESPOND_TOOL_NAME not in user_tool_names:
        tool_specs[RESPOND_TOOL_NAME] = respond_tooldef

    for t in tools_json:
        name = t["name"]
        # If the user defined respond explicitly, prefer forge's built-in (the
        # model expects `message` field, not an arbitrary shape).
        if name == RESPOND_TOOL_NAME and respond_tooldef is not None:
            tool_specs[name] = respond_tooldef
            continue
        # Otherwise build a ToolDef from the JSON schema.
        params_schema = t["parameters"]
        params_model = _json_schema_to_pydantic(name, params_schema)
        callable_fn = extra_callables.get(name) or _make_placeholder_callable(name, params_schema)
        tool_specs[name] = ToolDef(
            spec=ToolSpec(
                name=name,
                description=t["description"],
                parameters=params_model,
            ),
            callable=callable_fn,
        )

    return Workflow(
        name=definition["name"],
        description=definition.get("description", ""),
        tools=tool_specs,
        required_steps=definition.get("required_steps") or [],
        terminal_tool=definition["terminal_tool"],
        system_prompt_template=definition["system_prompt"],
    )


def _json_schema_to_pydantic(tool_name: str, schema: dict[str, Any]):
    """Convert a small subset of JSON Schema → pydantic BaseModel.

    Forge's ToolSpec.parameters expects a pydantic model class, not a raw
    JSON schema. This handles the common case (flat object with string /
    number / integer / boolean / array of primitives / nested objects one level
    deep). For exotic schemas, callers can pass a pre-built model via
    ``extra_callables`` instead.
    """
    from pydantic import BaseModel, Field, create_model
    from typing import get_args, get_origin

    if schema.get("type", "object") != "object":
        raise WorkflowValidationError(
            f"Tool {tool_name!r} parameters must be type 'object'"
        )
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    def _python_type(json_type: str, items: dict[str, Any] | None = None):
        jt = json_type.lower()
        if jt == "string":
            return str
        if jt in ("number",):
            return float
        if jt in ("integer",):
            return int
        if jt == "boolean":
            return bool
        if jt == "array":
            inner = items or {"type": "string"}
            inner_t = _python_type(inner.get("type", "string"), inner.get("items"))
            return list[inner_t]  # type: ignore[valid-type]
        if jt == "object":
            return dict
        return Any

    fields: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        py_type = _python_type(
            prop_def.get("type", "string"),
            prop_def.get("items"),
        )
        default = ... if prop_name in required else (prop_def.get("default", None))
        desc = prop_def.get("description", "")
        if default is ...:
            fields[prop_name] = (py_type, Field(..., description=desc))
        else:
            fields[prop_name] = (py_type, Field(default, description=desc))

    return create_model(f"{tool_name}_params", **fields)  # type: ignore[call-overload]


def _make_placeholder_callable(name: str, schema: dict[str, Any]):
    """Return a callable that lets the model continue without external execution.

    For tools without a user-supplied callable, this gives the model a graceful
    escape hatch: the call returns a placeholder message and the runner nudges
    the model toward the terminal tool. This matches the "structured task"
    pattern where tools are mostly schemas for the model to think about, not
    actions to execute.
    """
    def placeholder(**kwargs) -> str:
        return (
            f"Tool {name!r} is defined as a schema only (no callable registered). "
            f"Arguments received: {kwargs}. "
            f"If this was meant to produce a final answer, call the `respond` tool instead."
        )
    placeholder.__name__ = f"{name}_placeholder"
    return placeholder