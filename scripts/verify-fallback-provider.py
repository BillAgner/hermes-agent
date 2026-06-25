#!/usr/bin/env python3
"""Fallback provider smoke test.

Verifies that the configured fallback LLM provider (Ollama + Qwen3-VL GGUF
in current config) actually responds. Run on demand or via cron after the
primary provider has been touched (e.g. API key rotated).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "C:/Data/Hermes_0.17.0"))
CONFIG_PATH = HERMES_HOME / "config.yaml"
STATE_FILE = HERMES_HOME / "cron/output/fallback-verify/state.json"

TIMEOUT = 30


def send_telegram(text: str) -> None:
    try:
        subprocess.run(
            ["hermes", "send", "--to", "telegram", text],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


def read_fallback() -> dict | None:
    """Crude YAML read for fallback_providers[0]."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    fp = cfg.get("fallback_providers") or []
    return fp[0] if fp else None


def main() -> int:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fallback = read_fallback()
    if not fallback:
        print("[SILENT] no fallback provider configured")
        return 0

    base_url = fallback.get("base_url", "http://localhost:11434/v1")
    model = fallback.get("model", "")
    if not model:
        print("[SILENT] fallback model missing")
        return 0

    # Hit /v1/chat/completions with a one-token prompt.
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}],
        "max_tokens": 8,
        "temperature": 0,
    }).encode()

    t0 = time.time()
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        latency_ms = int((time.time() - t0) * 1000)
        content = data["choices"][0]["message"]["content"].strip()
    except urllib.error.URLError as e:
        msg = f"⚠️ *Fallback verify FAILED*\n\n`{url}` unreachable: `{e.reason}`\n\nModel: `{model}`\nHost: Z13"
        send_telegram(msg)
        print(f"FAIL: {e}")
        return 1
    except Exception as e:
        msg = f"⚠️ *Fallback verify FAILED*\n\n`{type(e).__name__}: {e}`\n\nModel: `{model}`\nHost: Z13"
        send_telegram(msg)
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1

    ok = bool(content) and latency_ms < (TIMEOUT * 1000)
    STATE_FILE.write_text(json.dumps({
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": ok,
        "model": model,
        "latency_ms": latency_ms,
        "content_preview": content[:50],
    }, indent=2), encoding="utf-8")

    if not ok:
        send_telegram(f"⚠️ *Fallback verify — slow/bad response*\n\nModel: `{model}`\nLatency: {latency_ms}ms\nContent: `{content[:60]}`")
        return 1

    # Silent on success unless first run
    if STATE_FILE.with_suffix(".firstrun").exists():
        print(f"OK ({latency_ms}ms) — {content!r}")
        return 0
    STATE_FILE.with_suffix(".firstrun").write_text("done", encoding="utf-8")
    msg = f"✅ *Fallback verified*\n\nModel: `{model}`\nURL: `{url}`\nLatency: {latency_ms}ms\nResponse: `{content!r}`\n\nHost: Z13"
    send_telegram(msg)
    print(f"OK ({latency_ms}ms) — {content!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
