# Rewiring a script from cloud LLM to local Ollama

A worked recipe for the kind of task that came up in the
`refresh_graphify.py` migration: a cron-driven script previously gated on
`GEMINI_API_KEY` (or similar) gets asked to use the local LLM instead.

The trap: the original gate was a **presence flag**, not an invocation.
Replacing `GEMINI_API_KEY` with "ollama has qwen2.5:14b" produces
exactly the same behavior — touch a marker file, do no LLM work — and
the user notices. This file is the fix.

## Step 1 — Read the original and decide which mode fits

Open the original script and ask: **what did the old gate actually
enable?** Three possibilities:

| If the original script... | Mode for the new one |
|----------------------------|----------------------|
| Calls the remote API to do work | Active invocation |
| Just checks the env var and lets a separate process do the work | Warmup ping (or stay presence-only with explicit justification) |
| The "remote API" was never actually called | You were already in presence-gate territory; flag this to the user before "porting" it |

The default when ambiguous is **warmup ping** — cheaper than full
invocation, but proves the model is actually engaged.

## Step 2 — Probe presence with `/api/tags`

Ollama exposes model inventory at `GET /api/tags`. Use stdlib `urllib`
(no extra deps):

```python
import json, urllib.request, urllib.error

def ollama_has_model(base_url: str, model: str, timeout_s: float = 3) -> bool:
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    names = {m.get("name", "") for m in data.get("models", [])}
    # Strip any digest suffix ("qwen2.5:14b@sha256:..." → "qwen2.5:14b")
    names |= {n.split("@", 1)[0] for n in names}
    return model in names or any(n.startswith(model + ":") for n in names)
```

The 3-second timeout matters: a cron that hangs on a dead Ollama blocks
the whole agent loop. Fail fast, log, exit 0.

## Step 3 — Engage the model with a `/api/generate` warmup

A presence check does not prove the model is responsive. To prove
engagement, do a single tiny inference:

```python
def ollama_warmup(base_url: str, model: str, prompt: str = "ping",
                  timeout_s: float = 30) -> tuple[bool, dict]:
    url = base_url.rstrip("/") + "/api/generate"
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 4, "temperature": 0},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False, {}
    return True, data  # data["response"], data.get("eval_count"), etc.
```

Notes:
- `num_predict: 4` keeps the warmup cheap (single-token output is enough
  to prove engagement; 4 covers the model loading + first decode).
- `temperature: 0` makes the output deterministic — easier to spot if
  the warmup is producing nonsense.
- `stream: false` returns one JSON object instead of NDJSON chunks; lets
  us keep the whole thing in `urllib`.

## Step 4 — Log enough to prove engagement

Log lines should answer "did the model actually run?" without needing
the reader to grep:

```
2026-06-26T20:54:54.673051+00:00 local LLM qwen2.5:14b present
  (http://localhost:11434); warmup took 1.83s, eval_count=4
```

Three useful fields beyond presence:

| Field | Why |
|-------|-----|
| `eval_count` (token count) | Proves the model produced output |
| `total_duration` / `eval_duration` | Distinguishes a real call from a cache hit |
| First 80 chars of `response` | Catches the "model loaded but producing garbage" failure mode |

## Step 5 — Update both the script AND the cron prompt

When the script's behavior changes, the cron job's prompt must change
too — the prompt is what tells the next agent what the script does.
Forgetting this is a common silent bug: the script behaves correctly,
but the agent reporting the result describes the old behavior.

## Step 6 — Verify all three paths before declaring done

| Path | Expect |
|------|--------|
| Ollama up + model loaded | exit 0, marker written, warmup logged with eval_count > 0 |
| Ollama up + wrong model | exit 0, no marker, log lists what *is* present |
| Ollama down | exit 0, no marker, log says unreachable |

If all three pass, the rewire is honest. If only the happy path passes
and the two failure paths weren't tested, the rewire probably drifted
back into presence-gate mode.

## Configuration defaults that worked

For the `refresh_graphify.py` migration, these defaults held up:

```python
DEFAULT_MODEL      = "qwen2.5:14b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_S  = 3        # presence probe
WARMUP_TIMEOUT_S   = 30       # /api/generate call
```

Env-var overrides for flexibility:

- `GRAPHIFY_MODEL` — change the model tag without editing the script
- `GRAPHIFY_OLLAMA` — point at a non-default ollama host (useful in containers)
- `GRAPHIFY_TIMEOUT` — bump the probe timeout if Ollama is on a slow network

## Anti-pattern — what NOT to do

```python
# BAD: only swaps the gate, doesn't engage the model
if not os.environ.get("GEMINI_API_KEY"):                # original
    return noop()
if not ollama_has_model(os.environ["OLLAMA_URL"], "qwen2.5:14b"):  # rewired, but identical behavior
    return noop()
touch_marker_file()
```

This compiles, passes "happy path" tests, exits 0 — and does no LLM
work. The user will catch it on the first run where they were watching
for actual engagement.

The fix:

```python
# GOOD: presence + warmup + marker
if not ollama_has_model(...):
    return noop()
ok, info = ollama_warmup(...)
if not ok:
    return noop()
log_warmup_with_token_count(info)
touch_marker_file()
```