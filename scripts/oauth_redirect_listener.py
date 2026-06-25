#!/usr/bin/env python3
"""oauth_redirect_listener.py — minimal localhost HTTP listener that captures
the OAuth ``code`` query parameter from Google's redirect and writes it to a
JSON file. The listener exits after the first successful capture (or on
timeout) so an agent can drive the OAuth dance without polling.

Usage::

    python oauth_redirect_listener.py [--port PORT] [--output FILE] [--timeout SECONDS]

Defaults::

    --port    19876
    --output  C:\\Data\\Hermes_0.17.0\\google_oauth_callback.json
    --timeout 300

Output JSON shape::

    {
        "code":        "4/0AeaYSHB...",
        "state":       "...",
        "scope":       "https://...",
        "received_at": "2026-06-20T22:30:00+00:00"
    }

A sidecar file ``<output>.port`` is written so an agent can read back the
chosen port if ``--port`` resolved to a higher free port (e.g. if 19876 was
taken, the script picked 19877 and writes that here).

Exit codes::

    0 — captured successfully
    1 — timed out without a capture
    2 — startup error (no free port, etc.)
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def find_free_port(preferred: int, span: int = 50) -> int:
    """Return the first free port in ``[preferred, preferred + span]``."""
    for port in [preferred] + list(range(preferred + 1, preferred + span)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in range {preferred}..{preferred + span}")


class _Handler(BaseHTTPRequestHandler):
    captured = False

    def log_message(self, *args, **kwargs):  # silence default access log
        pass

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler naming)
        if _Handler.captured:
            self.send_response(410)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"already captured\n")
            return

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        state_param = (params.get("state") or [""])[0]
        scope_param = (params.get("scope") or [""])[0]
        err = (params.get("error") or [""])[0]

        if err:
            body = (
                "<!DOCTYPE html><html><head><title>Auth error</title></head>"
                "<body style='font-family:-apple-system,sans-serif;padding:3em;"
                "max-width:560px;margin:0 auto;color:#24292f'>"
                "<h1 style='color:#cf222e'>Google returned an error</h1>"
                f"<p><code>{err}</code></p>"
                "<p>Return to Hermes — it will report the failure.</p>"
                "</body></html>"
            )
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            _Handler.captured = True
            self.server._shutdown_after = 0.3
            return

        if not code:
            body = (
                "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2em'>"
                "<h1>No code in URL</h1>"
                f"<p>Path: <code>{self.path}</code></p>"
                "</body></html>"
            )
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return

        payload = {
            "code": code,
            "state": state_param,
            "scope": scope_param,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        self.server.capture_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

        body = (
            "<!DOCTYPE html><html><head><title>Auth OK</title></head>"
            "<body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
            "padding:3em;max-width:560px;margin:0 auto;color:#24292f'>"
            "<h1 style='color:#1a7f37'>&#10003; Auth code captured</h1>"
            "<p>You can close this tab and return to Hermes. "
            "The OAuth flow will continue automatically.</p>"
            f"<p style='color:#57606a;font-size:0.9em'>Wrote to: "
            f"<code>{self.server.capture_path}</code></p>"
            "</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

        _Handler.captured = True
        # Schedule a hard exit from this process. Using os._exit bypasses any
        # pending handlers and the main loop's blocked handle_request() call.
        # The brief delay gives the HTTP response time to flush to the browser.
        import os as _os
        threading.Timer(0.5, lambda: _os._exit(0)).start()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=19876)
    p.add_argument(
        "--output",
        type=Path,
        default=Path(r"C:\Data\Hermes_0.17.0\google_oauth_callback.json"),
    )
    p.add_argument("--timeout", type=int, default=300, help="seconds before auto-exit (exit 1)")
    args = p.parse_args()

    try:
        port = find_free_port(args.port)
    except RuntimeError as e:
        print(f"oauth-redirect-listener: {e}", file=sys.stderr, flush=True)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Clear any stale capture from a previous run
    if args.output.exists():
        args.output.unlink()

    server = HTTPServer(("127.0.0.1", port), _Handler)
    server.timeout = args.timeout
    server.capture_path = args.output
    server._shutdown_after = None

    sidecar = args.output.with_suffix(args.output.suffix + ".port")
    sidecar.write_text(str(port), encoding="utf-8")

    print(f"oauth-redirect-listener: listening on http://127.0.0.1:{port}", flush=True)
    print(f"oauth-redirect-listener: capture -> {args.output}", flush=True)
    print(f"oauth-redirect-listener: timeout in {args.timeout}s", flush=True)

    def _maybe_shutdown():
        if server._shutdown_after is not None:
            threading.Timer(server._shutdown_after, server.shutdown).start()

    try:
        # Poll for shutdown signal from the handler
        import time
        deadline = time.monotonic() + args.timeout
        while not _Handler.captured:
            server.handle_request()
            if _Handler.captured:
                break
            if time.monotonic() > deadline:
                print("oauth-redirect-listener: timed out waiting for callback", file=sys.stderr, flush=True)
                return 1
        # Drain a final tick so the shutdown timer fires
        _maybe_shutdown()
        server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass
        print("oauth-redirect-listener: exiting", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
