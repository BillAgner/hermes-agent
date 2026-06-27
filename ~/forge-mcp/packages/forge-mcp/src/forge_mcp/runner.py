"""Workflow execution — thin wrapper around forge.WorkflowRunner.

Uses forge's ``on_message`` callback to capture a structured per-turn trace
(assistant tool calls, tool results, validation nudges, token usage) so the
MCP caller gets an audit-trail-quality record of what the local model did.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from forge import ContextManager, Message, MessageRole, MessageType, TieredCompact, WorkflowRunner

from forge_mcp.client import build_client
from forge_mcp.workflows import to_forge_workflow


async def run_workflow(
    *,
    workflow_def: dict[str, Any],
    user_message: str,
    model: str | None = None,
    max_iterations: int = 10,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    recommended_sampling: bool = True,
    extra_callables: dict[str, Any] | None = None,
    initial_messages: list[Message] | None = None,
) -> dict[str, Any]:
    """Execute a workflow and return a structured trace + final answer.

    Args:
        workflow_def: the JSON workflow definition (saved workflow or inline dict)
        user_message: the user's input prompt
        model: override the default model from FORGE_DEFAULT_MODEL
        max_iterations: cap on workflow turns (default 10)
        recommended_sampling: if True (default), apply card-recommended sampling
            per the forge sampling_defaults map. Set False to use raw backend defaults.
        extra_callables: dict of tool_name → python callable, for tools that have
            real implementations (from FORGE_TOOL_MODULES imports)
        initial_messages: optional pre-seeded conversation history (advanced)

    Returns:
        dict with keys: success, final_response, tool_calls, turns, tokens,
                        trace, model_used, duration_ms, error
    """
    start = time.monotonic()
    trace: list[dict[str, Any]] = []
    assistant_calls: list[dict[str, Any]] = []
    total_tokens = {"input": 0, "output": 0}
    turns = 0
    final_response: Any = None
    model_used: str | None = None

    def on_message(msg: Message) -> None:
        nonlocal turns
        # Track the most recent assistant message that produced tool calls
        if msg.role == MessageRole.ASSISTANT:
            turns += 1
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    assistant_calls.append({
                        "name": tc.name,
                        "arguments": tc.args,
                        "call_id": tc.call_id,
                    })
        # Append to trace with type + role tag for caller inspection
        trace.append({
            "role": msg.role.value,
            "type": msg.metadata.type.value if msg.metadata else "unknown",
            "tool_name": msg.tool_name,
            "content_preview": _preview(msg.content),
        })
        # Token accounting (best-effort — not all messages carry usage)
        token_est = getattr(msg.metadata, "token_estimate", None) if msg.metadata else None
        if isinstance(token_est, int):
            total_tokens["input"] += token_est

    try:
        client = build_client(
            model=model,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            recommended_sampling=recommended_sampling,
        )
        model_used = getattr(client, "model", None)
        workflow = to_forge_workflow(workflow_def, extra_callables=extra_callables)
        # Forge's WorkflowRunner requires a non-None context_manager — it calls
        # ctx.maybe_compact() on each iteration without a None guard. Build a
        # TieredCompact default sized for a typical short structured task.
        ctx = ContextManager(
            strategy=TieredCompact(keep_recent=4, compact_threshold=0.75),
            budget_tokens=8192,
        )
        runner = WorkflowRunner(
            client=client,
            context_manager=ctx,
            max_iterations=max_iterations,
            on_message=on_message,
        )

        final_response = await runner.run(
            workflow,
            user_message,
            initial_messages=initial_messages,
        )

        return {
            "success": True,
            "final_response": _final_response_to_jsonable(final_response),
            "tool_calls": assistant_calls,
            "turns": turns,
            "tokens": total_tokens,
            "trace": trace,
            "model_used": model_used,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "final_response": _final_response_to_jsonable(final_response) if final_response is not None else None,
            "tool_calls": assistant_calls,
            "turns": turns,
            "tokens": total_tokens,
            "trace": trace,
            "model_used": model_used,
            "duration_ms": int((time.monotonic() - start) * 1000),
        }


def _preview(value: Any, limit: int = 200) -> Any:
    """Short string preview for trace items."""
    if value is None:
        return None
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... ({len(value) - limit} more chars)"
    return value


def _final_response_to_jsonable(value: Any) -> Any:
    """Best-effort conversion of the terminal tool's return to a JSON-safe form."""
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool, str)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    return str(value)