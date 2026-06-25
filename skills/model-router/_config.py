"""
_config.py — centralized config access for the model-router skill.

All environment-variable reads in model-router go through these helpers
instead of calling `os.environ.get()` directly. Reasons:

1. **Single source of truth for config keys.** New env vars get added
   in one place, with type, default, and a docstring describing use.
2. **Testability.** Tests can monkeypatch `model_router._config.get`,
   `_config.get_int`, etc. without touching the global `os.environ`.
3. **Audit trail.** `grep -rn "os.environ" model-router/` shows only
   this file — every config read is documented and reviewable.
4. **Cleaner caller code.** `get("OLLAMA_URL", "http://...")` reads
   better than `os.environ.get("OLLAMA_URL", "http://...")`.

These helpers do invoke `os.environ.get()` / `os.getenv()` internally —
the static security scanner (`tools/skills_guard.py`) flags those as
"config access, not exfiltration by itself" (high severity, not
critical). With `skills.guard_agent_created` enabled, the model-router
skill is in the verify-by-allowlist category; see the `guard_allowlist`
config and the comment in `_security_scan_skill` for details.
"""
from __future__ import annotations

import os
from typing import Optional


def get(name: str, default: str = "") -> str:
    """Read a config value from the environment. Returns ``default`` if unset."""
    return os.environ.get(name, default)


def get_optional(name: str) -> Optional[str]:
    """Read a config value from the environment. Returns None if unset/empty."""
    value = os.environ.get(name)
    return value if value else None


def require(name: str) -> str:
    """Read a required config value. Raises ``RuntimeError`` if unset/empty.

    Use for values the skill can't function without (e.g. API keys for
    paid services). Prefer ``get`` with a sensible default for optional
    config.
    """
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is not set; this skill requires it. "
            f"Set it in the environment or your shell profile."
        )
    return value


def get_int(name: str, default: int) -> int:
    """Read an integer config value. ``default`` is returned if unset."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"{name}={raw!r} is not a valid integer"
        ) from None


def get_float(name: str, default: float) -> float:
    """Read a float config value. ``default`` is returned if unset."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"{name}={raw!r} is not a valid float"
        ) from None
