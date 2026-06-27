"""Backend client factory for forge-mcp.

Given the env-var configuration, returns a forge LLMClient ready to plug into
WorkflowRunner. Three backends are supported:

- ollama       — uses forge.clients.OllamaClient   (POST /api/chat, native Ollama API)
- llamafile    — uses forge.clients.LlamafileClient (POST /v1/chat/completions, but with
                 the llamacpp gguf path baked into mode='native' for proper tool-call
                 chat-template handling)
- openai-compat — uses forge.clients.OpenAICompatClient (POST /v1/chat/completions,
                  works against llama-server, vLLM, LM Studio, or any OpenAI-wire server)

Tool-callable ``register_tools()`` modules (if any) are imported once at MCP
startup and their results merged into the registry passed to workflow_runner.

Note on sampling-defaults lookup: forge's registry keys models by their
quant-suffixed form (``qwen3:8b-q4_K_M``), but Ollama users typically pass
the bare tag (``qwen3:8b``). We smart-resolve by trying the bare name first,
then common quant suffixes, so recommended_sampling works with both naming
conventions.
"""

from __future__ import annotations  # safe here — only consumed by Python at runtime, not FastMCP

import importlib
import logging
import os
import warnings
from pathlib import Path
from typing import Any

from forge.clients import LlamafileClient, OllamaClient, OpenAICompatClient
from forge.clients.sampling_defaults import (
    MODEL_SAMPLING_DEFAULTS,
    apply_sampling_defaults,
    get_sampling_defaults,
)


logger = logging.getLogger(__name__)

# Common quant suffixes forge knows about, ordered most-likely first.
_QUANT_SUFFIXES = [
    "-q4_K_M", "-Q4_K_M",
    "-q8_0",   "-Q8_0",
    "-q4_0",   "-Q4_0",
    "-q4_1",   "-Q4_1",
    "-q5_K_M", "-Q5_K_M",
    "-q6_K",   "-Q6_K",
]


def resolve_model_name(name: str) -> tuple[str, dict[str, Any]]:
    """Find the registry key closest to ``name``.

    Tries the bare name first (Ollama convention), then appends common quant
    suffixes (llama-server / GGUF convention). Returns the first hit plus the
    sampling defaults dict; if nothing matches, returns (name, {}).
    """
    if name in MODEL_SAMPLING_DEFAULTS:
        return name, dict(MODEL_SAMPLING_DEFAULTS[name])
    for suf in _QUANT_SUFFIXES:
        candidate = name + suf
        if candidate in MODEL_SAMPLING_DEFAULTS:
            logger.info(
                "Resolved model %r → %r for sampling-defaults lookup",
                name, candidate,
            )
            return candidate, dict(MODEL_SAMPLING_DEFAULTS[candidate])
    return name, {}


def resolve_sampling_defaults(name: str) -> dict[str, Any]:
    """Public resolver — returns sampling defaults for ``name``, smart-resolving quant suffixes."""
    _, defaults = resolve_model_name(name)
    return defaults


BACKEND_DEFAULTS: dict[str, dict[str, str]] = {
    "ollama":       {"base_url": "http://localhost:11434"},
    "llamafile":    {"base_url": "http://localhost:8080/v1"},
    "openai-compat": {"base_url": "http://localhost:8080/v1"},
}


def get_config() -> dict[str, Any]:
    """Read FORGE_* env vars and return a normalized config dict."""
    backend = os.environ.get("FORGE_BACKEND", "ollama").strip().lower()
    if backend not in BACKEND_DEFAULTS:
        raise ValueError(
            f"FORGE_BACKEND={backend!r} is not supported. "
            f"Choose one of: {', '.join(BACKEND_DEFAULTS)}"
        )
    base_url = os.environ.get("FORGE_BASE_URL", "").strip() or BACKEND_DEFAULTS[backend]["base_url"]
    return {
        "backend": backend,
        "base_url": base_url,
        "default_model": os.environ.get("FORGE_DEFAULT_MODEL", "").strip(),
        "workflows_dir": os.environ.get(
            "FORGE_WORKFLOWS_DIR",
            str(Path.home() / ".hermes" / "forge" / "workflows"),
        ),
        "tool_modules": os.environ.get("FORGE_TOOL_MODULES", "").strip(),
    }


def build_client(
    *,
    model: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    recommended_sampling: bool = False,
) -> Any:
    """Construct a forge LLMClient from env config + per-call overrides.

    ``recommended_sampling=True`` is the recommended way to use forge: it pulls
    card-recommended values from the per-model map. Per-call kwargs override
    the map field-by-field (None values pass through).

    Smart-resolves Ollama-style bare model names (``qwen3:8b``) to their
    quant-suffixed registry keys (``qwen3:8b-q4_K_M``) so the sampling map
    lookup succeeds even when the user passes the Ollama tag rather than the
    GGUF-stem form. Falls back gracefully to backend defaults if neither
    form is registered.
    """
    cfg = get_config()
    chosen_model = (model or cfg["default_model"]).strip()
    if not chosen_model:
        raise ValueError(
            "No model specified and FORGE_DEFAULT_MODEL is unset. "
            "Either pass `model=` to forge_run_workflow / forge_run_inline, "
            "or set FORGE_DEFAULT_MODEL env var on the MCP subprocess."
        )

    # Smart-resolve for sampling defaults: try the bare name first, then
    # quant-suffixed variants. If we find a registered key, apply those
    # defaults directly (bypassing the strict-mode constructor flag) so
    # unknown-model doesn't raise. If we don't, set recommended_sampling=False
    # so the constructor falls back to backend defaults silently.
    resolved_key, resolved_defaults = resolve_model_name(chosen_model)
    if recommended_sampling and resolved_defaults:
        # Apply smart-resolved defaults; caller's explicit non-None kwargs win
        if temperature is None:
            temperature = resolved_defaults.get("temperature")
        if top_p is None:
            top_p = resolved_defaults.get("top_p")
        if top_k is None:
            top_k = resolved_defaults.get("top_k")
        # Don't pass recommended_sampling=True to the client — we already
        # applied the defaults; otherwise it'd try a second lookup with strict=True
        # on the bare name and raise on Ollama-style names.
        recommended_sampling_pass = False
        if resolved_key != chosen_model:
            logger.info(
                "Smart-resolved %r → %r for sampling defaults",
                chosen_model, resolved_key,
            )
    else:
        recommended_sampling_pass = recommended_sampling

    common: dict[str, Any] = {
        "model": chosen_model,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "recommended_sampling": recommended_sampling_pass,
    }
    if cfg["backend"] == "ollama":
        return OllamaClient(base_url=cfg["base_url"], **common)
    if cfg["backend"] == "llamafile":
        # LlamafileClient requires a gguf path; for llama-server compatibility
        # we use OpenAICompatClient under the same name — same wire protocol,
        # broader compatibility (handles llama-server, vLLM, LM Studio).
        return OpenAICompatClient(base_url=cfg["base_url"], **common)
    if cfg["backend"] == "openai-compat":
        return OpenAICompatClient(base_url=cfg["base_url"], **common)
    raise ValueError(f"Unknown backend {cfg['backend']!r}")  # unreachable


def load_tool_modules() -> dict[str, Any]:
    """Import user-supplied callable tool modules.

    Each module must expose ``register_tools() -> dict[str, ToolDef]``. Modules
    are imported by dotted-path, separated by ':' (POSIX) or ';' (Windows-safe
    fallback since ':' on Windows is illegal in module paths anyway).

    Returns an empty dict if FORGE_TOOL_MODULES is unset.
    """
    cfg = get_config()
    spec = cfg["tool_modules"]
    if not spec:
        return {}
    sep = ";" if ";" in spec else ":"
    out: dict[str, Any] = {}
    for mod_name in spec.split(sep):
        mod_name = mod_name.strip()
        if not mod_name:
            continue
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # pragma: no cover — surface as warning, not crash
            import warnings
            warnings.warn(f"forge-mcp: failed to import tool module {mod_name!r}: {exc}")
            continue
        register = getattr(mod, "register_tools", None)
        if register is None:
            import warnings
            warnings.warn(
                f"forge-mcp: {mod_name!r} has no register_tools() — skipping"
            )
            continue
        try:
            tools = register()
        except Exception as exc:  # pragma: no cover
            import warnings
            warnings.warn(
                f"forge-mcp: {mod_name!r}.register_tools() raised: {exc}"
            )
            continue
        if not isinstance(tools, dict):
            import warnings
            warnings.warn(
                f"forge-mcp: {mod_name!r}.register_tools() returned {type(tools).__name__}, "
                f"expected dict[str, ToolDef] — skipping"
            )
            continue
        # Prefix tool names with module shortname to avoid collisions
        short = mod_name.rsplit(".", 1)[-1]
        for tool_name, tool_def in tools.items():
            out[f"{short}.{tool_name}"] = tool_def
    return out


async def health_check() -> dict[str, Any]:
    """Async health probe — used by forge_health tool."""
    import httpx
    cfg = get_config()
    info: dict[str, Any] = {
        "success": True,
        "version": __version__ if False else None,  # filled by caller
        "config": {
            "backend": cfg["backend"],
            "base_url": cfg["base_url"],
            "default_model": cfg["default_model"] or None,
            "workflows_dir": cfg["workflows_dir"],
            "tool_modules": cfg["tool_modules"] or None,
        },
    }
    # Probe the backend
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            if cfg["backend"] == "ollama":
                r = await http.get(f"{cfg['base_url']}/api/tags")
                r.raise_for_status()
                models = [m.get("name") for m in r.json().get("models", [])]
                info["backend_status"] = "reachable"
                info["models_available"] = models
                info["default_model_loaded"] = (
                    cfg["default_model"] in models if cfg["default_model"] else None
                )
            else:
                # OpenAI-wire: GET /v1/models
                r = await http.get(f"{cfg['base_url']}/models")
                r.raise_for_status()
                data = r.json()
                models = [m.get("id") for m in data.get("data", [])]
                info["backend_status"] = "reachable"
                info["models_available"] = models
    except Exception as exc:
        info["success"] = False
        info["backend_status"] = f"unreachable: {type(exc).__name__}: {exc}"
    return info