"""
maf_mcp.server -- FastMCP server exposing Microsoft Agent Framework (MAF)
capabilities as MCP tools for Hermes.

Tools:
  - maf_health: confirm MAF is loadable + report versions
  - maf_create_agent: persist a named agent profile (system_prompt + model)
  - maf_chat: stateless single-turn chat with a named profile
  - maf_run_workflow: multi-step sequence of agent invocations
  - maf_list_models: report configured LLM clients (Azure / OpenAI / Foundry / Anthropic / Bedrock)

NOTES:
  - Per Hermes MCP conventions: NO `from __future__ import annotations`, NO
    `Optional[X]` (use bare `X = None`).
  - State persists in `data/maf_state.json` (created on first write).
  - API keys are read from environment at tool-call time, NOT at import,
    so a missing key is a runtime error per-call, not a startup failure.
"""

import json
import os
import asyncio
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("maf")

DATA_DIR = Path(os.environ.get("MAF_MCP_DATA_DIR", Path.home() / ".maf_mcp"))
STATE_FILE = DATA_DIR / "maf_state.json"

# ---- state helpers ---------------------------------------------------------


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"agents": {}, "runs": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"agents": {}, "runs": []}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _truncate(s: str, n: int = 8000) -> str:
    if len(s) <= n:
        return s
    return s[: n - 200] + "\n\n... [truncated; full output " + str(len(s)) + " chars] ..."


# ---- env detection ---------------------------------------------------------


def _detect_clients() -> dict[str, str]:
    """Return a dict of which MAF chat-client env-vars are set.

    Hermes routes through OpenRouter, so the primary 'client' is OpenRouter
    (OpenAI-compatible API). Local Ollama is the fallback. Azure / Bedrock
    / Foundry keys are surfaced only if explicitly configured.
    """
    candidates = {
        "openrouter": "OPENROUTER_API_KEY",
        "ollama": "OLLAMA_BASE_URL",
        "azure_openai": "AZURE_OPENAI_API_KEY",
        "azure_endpoint": "AZURE_OPENAI_ENDPOINT",
        "foundry_project": "FOUNDRY_PROJECT_ENDPOINT",
        "anthropic": "ANTHROPIC_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",
        "github_copilot": "GITHUB_TOKEN",
    }
    return {name: env for name, env in candidates.items() if os.environ.get(env)}


def _primary_model() -> str:
    """Resolve the default LLM model: HERMES_PRIMARY_MODEL env var > config file."""
    env_override = os.environ.get("HERMES_PRIMARY_MODEL")
    if env_override:
        return env_override
    # Read config.yaml if present
    cfg_paths = [
        Path(os.environ.get("HERMES_CONFIG", "")),
        Path.home() / ".hermes" / "config.yaml",
        Path("C:/Data/Hermes/config.yaml"),
    ]
    for p in cfg_paths:
        if p and p.exists():
            try:
                import yaml

                cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                model_block = cfg.get("model") or {}
                if isinstance(model_block, dict) and model_block.get("default"):
                    return str(model_block["default"])
            except Exception:
                pass
    return "minimax/minimax-m3"


def _build_client(model_hint: str = "auto"):
    """Return the best MAF chat-client for the configured environment.

    Prefers OpenRouter (OpenAI-compatible) when OPENROUTER_API_KEY is set,
    because that's what the Hermes agent itself uses. Falls back to local
    Ollama (also OpenAI-compatible) when only OLLAMA_BASE_URL is set.
    """
    from agent_framework.openai import OpenAIChatClient

    if os.environ.get("OPENROUTER_API_KEY"):
        return OpenAIChatClient(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            model=model_hint if model_hint != "auto" else _primary_model(),
        )
    if os.environ.get("OLLAMA_BASE_URL"):
        return OpenAIChatClient(
            api_key="ollama",
            base_url=os.environ["OLLAMA_BASE_URL"],
            model=model_hint if model_hint != "auto" else "qwen3-vl:8b-thinking-q4_K_M",
        )
    if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        from agent_framework.openai import OpenAIChatClient as _OACC  # noqa: F401
        # MAF handles Azure via the same OpenAIChatClient with extra kwargs;
        # left as a placeholder for Azure-routed deployments.
        return OpenAIChatClient(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
            model=model_hint if model_hint != "auto" else _primary_model(),
        )
    return None


# ---- tool: maf_health -------------------------------------------------------


@mcp.tool()
def maf_health() -> dict[str, Any]:
    """Confirm MAF is importable + report version, which chat clients are
    configured, and the resolved primary model (Hermes default)."""
    result: dict[str, Any] = {
        "status": "ok",
        "data_dir": str(DATA_DIR),
        "state_file": str(STATE_FILE),
        "state_file_exists": STATE_FILE.exists(),
        "configured_clients": _detect_clients(),
        "primary_model": _primary_model(),
    }
    try:
        import agent_framework  # noqa: F401

        result["agent_framework_version"] = getattr(agent_framework, "__version__", "unknown")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"agent_framework import failed: {e!r}"
    return result


# ---- tool: maf_create_agent -------------------------------------------------


@mcp.tool()
def maf_create_agent(name: str, instructions: str, model: str = "auto") -> dict[str, Any]:
    """Persist a named agent profile (system prompt + model hint).

    Args:
        name: short profile name (will be used as the agent_id in maf_chat)
        instructions: system prompt for the agent
        model: model name to use (default "auto" picks the first configured client)
    """
    state = _load_state()
    state["agents"][name] = {"instructions": instructions, "model": model}
    _save_state(state)
    return {"agent_id": name, "model": model, "instructions_chars": len(instructions), "stored": True}


# ---- tool: maf_chat ---------------------------------------------------------


@mcp.tool()
async def maf_chat(
    agent_id: str,
    prompt: str,
    model: str = "auto",
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Stateless single-turn chat using a stored agent profile.

    Defaults to Hermes's primary LLM routing (OpenRouter). Pass an explicit
    model name (e.g. "anthropic/claude-sonnet-4") to override.

    Args:
        agent_id: profile name created by maf_create_agent
        prompt: user message
        model: override the model stored in the profile; "auto" -> primary
        max_tokens: cap on the response length
    """
    state = _load_state()
    agent = state["agents"].get(agent_id)
    if agent is None:
        return {"status": "error", "error": f"unknown agent_id: {agent_id}"}

    chosen_model = model if model != "auto" else agent.get("model", "auto")

    try:
        from agent_framework import Agent
    except Exception as e:
        return {"status": "error", "error": f"MAF import failed: {e!r}"}

    client = _build_client(model_hint=chosen_model)
    if client is None:
        return {
            "status": "error",
            "error": "no chat client configured (need OPENROUTER_API_KEY, OLLAMA_BASE_URL, or AZURE_*)",
            "configured_clients": _detect_clients(),
        }

    try:
        agent_obj = Agent(client=client, instructions=agent["instructions"])
        response = await agent_obj.run(prompt)
        text = str(response)
        run_record = {"agent_id": agent_id, "prompt_chars": len(prompt), "model": chosen_model, "ok": True}
        state["runs"].append(run_record)
        state["runs"] = state["runs"][-50:]
        _save_state(state)
        return {
            "status": "ok",
            "agent_id": agent_id,
            "model": chosen_model,
            "response": _truncate(text, max_tokens * 4),
        }
    except Exception as e:
        state["runs"].append({"agent_id": agent_id, "ok": False, "error": repr(e)})
        state["runs"] = state["runs"][-50:]
        _save_state(state)
        return {"status": "error", "error": repr(e)}


# ---- tool: maf_run_workflow -------------------------------------------------


@mcp.tool()
async def maf_run_workflow(steps: list[dict[str, str]]) -> dict[str, Any]:
    """Run a sequence of agent calls; each step uses an existing agent_id.

    Args:
        steps: list of {"agent_id": "<name>", "prompt": "<text>"} dicts
    """
    if not steps:
        return {"status": "error", "error": "steps list is empty"}

    outputs: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        aid = step.get("agent_id", "")
        prompt = step.get("prompt", "")
        if not aid or not prompt:
            outputs.append({"step": i, "status": "error", "error": "missing agent_id or prompt"})
            continue
        result = await maf_chat.fn(agent_id=aid, prompt=prompt)  # type: ignore[attr-defined]
        outputs.append({"step": i, "agent_id": aid, **result})
        if result.get("status") != "ok":
            outputs.append({"step": i, "status": "aborted", "reason": f"step {i} failed"})
            return {"status": "aborted", "completed_steps": i, "outputs": outputs}
    return {"status": "ok", "completed_steps": len(outputs), "outputs": outputs}


# ---- tool: maf_list_models --------------------------------------------------


@mcp.tool()
def maf_list_models() -> dict[str, Any]:
    """Report which LLM clients are wired (env-key detection) and the resolved
    primary model Hermes uses."""
    return {
        "configured_clients": _detect_clients(),
        "primary_model": _primary_model(),
        "openrouter_base_url": "https://openrouter.ai/api/v1",
    }


# ---- entrypoint -------------------------------------------------------------


def main() -> None:
    """Entry point for the maf-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()