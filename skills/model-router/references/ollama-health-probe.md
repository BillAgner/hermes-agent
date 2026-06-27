# Ollama Health Probe — copy-paste recipe

A drop-in health probe for gating any script (cron, daemon, agent loop)
on a local Ollama model actually being loaded. Extracted from
`scripts/refresh_graphify.py` after the 2026-06-26 rewire from
`GEMINI_API_KEY` → `qwen2.5:14b` on `http://localhost:11434`.

## The probe

```python
"""Ollama model-availability probe.

Returns (ok: bool, detail: str) where detail is a short human-readable
note suitable for logging. Stdlib only — no `requests`, no `httpx`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Tuple

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL      = "qwen2.5:14b"
DEFAULT_TIMEOUT_S  = 3


def ollama_has_model(
    url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Tuple[bool, str]:
    """Probe `/api/tags` for the named model."""
    api_url = url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(api_url, timeout=timeout_s) as resp:
            payload = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"ollama unreachable at {url} ({exc.__class__.__name__})"

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return False, f"ollama returned malformed /api/tags JSON ({exc})"

    names = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]

    # Tag-normalization gotcha: Ollama reports the same model under
    # multiple tag conventions. `ollama list` shows both
    #   qwen2.5:14b
    #   hf.co/unsloth/Qwen2.5-14B-Instruct-GGUF:Q4_K_M
    # for the same physical file. Two-tier match handles both.
    present = any(
        n == model or n.startswith(model + ":") for n in names
    )
    if not present:
        joined = ", ".join(sorted(set(names)))
        return False, f"model {model!r} not found in /api/tags (have: {joined})"
    return True, f"ollama at {url} has {model}"
```

## Standard wiring

```python
def main() -> int:
    ollama_url = os.environ.get("GRAPHIFY_OLLAMA", DEFAULT_OLLAMA_URL)
    model      = os.environ.get("GRAPHIFY_MODEL",  DEFAULT_MODEL)
    timeout    = float(os.environ.get("GRAPHIFY_TIMEOUT", str(DEFAULT_TIMEOUT_S)))

    ok, detail = ollama_has_model(ollama_url, model, timeout)
    if not ok:
        msg = f"local LLM unavailable; skipping — {detail}"
        log(msg)
        return 0      # noop is a feature, not a failure

    # ... do the real work here ...
    touch_marker_file()
    log(f"local LLM {model} present ({ollama_url}); marker written")
    return 0
```

## Env-var contract

Every script that uses this probe should expose three overrides so it
can be tested and re-targeted without code edits:

| Var | Default | Purpose |
|-----|---------|---------|
| `GRAPHIFY_OLLAMA` | `http://localhost:11434` | Ollama base URL |
| `GRAPHIFY_MODEL` | `qwen2.5:14b` | model tag to gate on |
| `GRAPHIFY_TIMEOUT` | `3` | HTTP timeout in seconds |

Rename the prefix to match the script's namespace (`COMEX_OLLAMA`,
`BRIEF_MODEL`, etc.) — the names above are for the graphify job specifically.

## Verification recipe — all three branches must pass

```bash
# 1. Happy path: ollama up, model present
python your_script.py
# expect: exit 0, log "marker written", marker file exists

# 2. Wrong model: ollama up, model missing
GRAPHIFY_MODEL="nonexistent:99b" python your_script.py
# expect: exit 0, log "model 'nonexistent:99b' not in /api/tags (have: ...)", no marker

# 3. Ollama unreachable
GRAPHIFY_OLLAMA="http://localhost:1" GRAPHIFY_TIMEOUT=1 python your_script.py
# expect: exit 0, log "ollama unreachable at http://localhost:1 (URLError)", no marker
```

All three exit 0. Non-zero exits should be reserved for "probe succeeded
but the actual work failed" — never for "model not loaded" (that's a
known-good state, not an error).

## Cross-reference

- Skill: `model-router` — see SKILL.md § "Rewire an existing cloud-gated
  script to gate on local Ollama" for the broader procedure.
- Skill: `qmd-install` — uses the same Ollama inventory (bge-m3,
  bge-reranker-v2-m3, qwen3:8b) for hybrid BM25+vector search.
- Skill: `llama-cpp` — covers the underlying GGUF inference engine;
  Ollama is the HTTP wrapper around it on this host.