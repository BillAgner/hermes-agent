"""
judge.py — LLM-as-judge equivalence scoring for shadow-validation.

Use this when embedding cosine similarity is too noisy for the task
(open-ended generation, creative tasks, formatting-heavy tasks).

Design notes:
- We ask the judge for a 0-1 score + a one-sentence reason. JSON output.
- The judge is the cloud primary model by default (M3 via openrouter), but
  any openrouter model id works via JUDGE_MODEL env var.
- The judge sees the two responses labelled "Response A" and "Response B"
  (not "cloud" / "local") to reduce bias toward either one.
- We use temperature=0 for determinism and JSON-mode via the response_format
  field when the model supports it. We fall back to text + json.loads on
  failure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Tuple

import requests

from _config import get, get_float, get_int, require

# Optional: load $HERMES_HOME/.env so OPENROUTER_API_KEY is available when
# this module is imported in isolation (smoke tests, direct CLI use). The
# helper uses python-dotenv if installed; missing is fine.
try:
    from dotenv import load_dotenv as _load_dotenv
    _hermes_home = Path(get("HERMES_HOME")).expanduser()
    _env_path = _hermes_home / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
except ImportError:
    pass

OPENROUTER_URL = get("JUDGE_URL", "https://openrouter.ai/api/v1/chat/completions")
JUDGE_MODEL = get("JUDGE_MODEL", "minimax/minimax-m3")
JUDGE_TIMEOUT = get_int("JUDGE_TIMEOUT", 180)

# Pass threshold for LLM-as-judge. 0.80 means "same conclusions, slight
# wording differences OK" — generous but not permissive of factual drift.
JUDGE_PASS_THRESHOLD = get_float("JUDGE_PASS_THRESHOLD", 0.80)


JUDGE_PROMPT = """\
You are an impartial equivalence judge. Two AI assistants answered the same task \
below. Decide whether their responses are functionally equivalent for the same \
downstream use.

Task: {task_kind}
Prompt: {prompt}

Response A:
{a}

Response B:
{b}

Rate the equivalence on this scale:
  1.0  functionally identical, interchangeable
  0.8  same conclusions, slightly different wording/format
  0.5  same domain and approach, meaningful differences in detail
  0.2  different approach or different conclusions
  0.0  completely different or wrong

Output STRICT JSON, no markdown, no preamble:
{{"score": <float 0-1>, "reasoning": "<one short sentence>"}}
"""


def _get_api_key() -> str:
    return require("OPENROUTER_API_KEY")


def _try_json_loads(s: str) -> dict[str, Any] | None:
    """Tolerant JSON parser. Handles ```json ... ``` fences, leading prose,
    trailing truncation, etc. Returns the parsed dict or None."""
    if not s:
        return None
    # Strip ```json ... ``` fences
    s = re.sub(r"^\s*```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    # Try direct parse first
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Pull the first {...} block
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Last resort: response was truncated. Try to extract the score field
    # even if the rest of the JSON is incomplete.
    score_m = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', s)
    reason_m = re.search(r'"reasoning"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)', s)
    if score_m:
        return {
            "score": float(score_m.group(1)),
            "reasoning": (reason_m.group(1) if reason_m else "") + " [truncated]",
        }
    return None


def judge(
    a: str,
    b: str,
    prompt: str = "",
    task_kind: str = "(unspecified)",
    *,
    model: str | None = None,
) -> Tuple[float, str, str]:
    """Return (score, reasoning, raw_response). Score is in [0, 1].

    Raises RuntimeError if the judge call fails completely (so the caller
    can choose to fall back to embedding).
    """
    api_key = _get_api_key()
    model = model or JUDGE_MODEL
    user_prompt = JUDGE_PROMPT.format(
        task_kind=task_kind, prompt=prompt[:1000], a=a[:2000], b=b[:2000]
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.0,
        # 4096 leaves headroom for the judge's reasoning tokens; the actual
        # answer is ~50 tokens. Bumping this is cheap — judge calls are not
        # in any hot path.
        "max_tokens": 4096,
    }
    # Skip response_format: many chat models (including M3) handle JSON output
    # reliably via the prompt alone, and asking for response_format triggers
    # provider-specific structured output that not all models support.
    r = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=JUDGE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    raw = data["choices"][0]["message"]["content"]
    parsed = _try_json_loads(raw)
    if parsed is None or "score" not in parsed:
        raise RuntimeError(f"judge returned unparseable response: {raw[:300]!r}")
    try:
        score = float(parsed["score"])
    except (TypeError, ValueError):
        raise RuntimeError(f"judge returned non-numeric score: {parsed.get('score')!r}")
    score = max(0.0, min(1.0, score))  # clamp
    reasoning = str(parsed.get("reasoning", ""))[:300]
    return score, reasoning, raw


def passed(score: float) -> bool:
    return score >= JUDGE_PASS_THRESHOLD
