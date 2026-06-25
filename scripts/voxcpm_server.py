"""Persistent VoxCPM2 TTS HTTP server.

Loads the VoxCPM2 model once on startup and keeps it resident in memory.
Exposes a simple HTTP API that the Hermes TTS command calls.

Usage:
  python voxcpm_server.py [--port 9121] [--host 127.0.0.1]

Endpoints:
  GET  /health           -> {"ok": true, "model_loaded": true}
  POST /synthesize       -> WAV audio bytes (Content-Type: audio/wav)
    body: {"text": "...", "voice_description": "...", "cfg_value": 2.0, "timesteps": 10}
"""
import argparse
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve VoxCPM package from its own venv before anything else
# ---------------------------------------------------------------------------
VOXCPM_ROOT = Path(__file__).parent.parent / "~" / "VoxCPM"
VOXCPM_VENV = VOXCPM_ROOT / ".venv"
VOXCPM_SITE = VOXCPM_VENV / "Lib" / "site-packages"
if str(VOXCPM_SITE) not in sys.path:
    sys.path.insert(0, str(VOXCPM_SITE))

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voxcpm-server] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("voxcpm_server")

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
MODEL_ID      = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
DEFAULT_VOICE = os.environ.get("VOXCPM_DEFAULT_VOICE_DESCRIPTION", "calm English male voice")
CFG_VALUE     = float(os.environ.get("VOXCPM_CFG_VALUE", "2.0"))
TIMESTEPS     = int(os.environ.get("VOXCPM_TIMESTEPS", "10"))
DENOISER_ID   = os.environ.get("VOXCPM_DENOISER_ID", "")
LOAD_DENOISER = DENOISER_ID.strip().lower() not in ("", "none", "false", "0", "off")
OPTIMIZE      = os.environ.get("VOXCPM_OPTIMIZE", "0").strip().lower() not in ("0", "false", "off")
DEVICE        = os.environ.get("VOXCPM_DEVICE") or None
CACHE_DIR     = os.environ.get("VOXCPM_CACHE_DIR") or None

_model = None
_model_load_time: float = 0.0


def _load_model():
    global _model, _model_load_time
    if _model is not None:
        return _model
    log.info("Loading VoxCPM2 model '%s' (this takes ~30–90s on first run)…", MODEL_ID)
    t0 = time.time()
    from voxcpm import VoxCPM  # noqa: PLC0415
    _model = VoxCPM.from_pretrained(
        hf_model_id=MODEL_ID,
        load_denoiser=LOAD_DENOISER,
        zipenhancer_model_id=DENOISER_ID if LOAD_DENOISER else None,
        optimize=OPTIMIZE,
        device=DEVICE,
        cache_dir=CACHE_DIR,
    )
    _model_load_time = time.time() - t0
    log.info("VoxCPM2 loaded in %.1fs on device=%s", _model_load_time, DEVICE or "auto")
    return _model


def synthesize(text: str, voice_description: str = "", cfg_value: float = CFG_VALUE, timesteps: int = TIMESTEPS) -> bytes:
    """Synthesize text → WAV bytes using the resident model."""
    import numpy as np
    import soundfile as sf

    voice = voice_description.strip() or DEFAULT_VOICE
    final_text = f"({voice}) {text}" if voice and not text.startswith("(") else text

    model = _load_model()
    t0 = time.time()
    wav = model.generate(text=final_text, cfg_value=cfg_value, inference_timesteps=timesteps)
    wav = np.asarray(wav, dtype=np.float32).squeeze()
    sample_rate = int(getattr(model.tts_model, "sample_rate", 48000))
    elapsed = time.time() - t0
    duration = len(wav) / sample_rate
    log.info("Synthesized %.2fs audio in %.2fs (RTF=%.2f)", duration, elapsed, elapsed / max(duration, 0.001))

    buf = io.BytesIO()
    sf.write(buf, wav, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# HTTP server (stdlib http.server — no extra deps beyond VoxCPM's venv)
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "model_loaded": _model is not None}).encode()
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        if self.path != "/synthesize":
            self._send(404, "text/plain", b"Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, "application/json", b'{"error":"invalid JSON"}')
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._send(400, "application/json", b'{"error":"text is required"}')
            return

        try:
            wav_bytes = synthesize(
                text=text,
                voice_description=payload.get("voice_description", ""),
                cfg_value=float(payload.get("cfg_value", CFG_VALUE)),
                timesteps=int(payload.get("timesteps", TIMESTEPS)),
            )
            self._send(200, "audio/wav", wav_bytes)
        except Exception as exc:
            log.exception("Synthesis failed")
            body = json.dumps({"error": str(exc)}).encode()
            self._send(500, "application/json", body)


def main():
    parser = argparse.ArgumentParser(description="Persistent VoxCPM2 TTS server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9121)
    parser.add_argument("--preload", action="store_true", default=True,
                        help="Load model at startup (default: True)")
    args = parser.parse_args()

    if args.preload:
        _load_model()

    server = HTTPServer((args.host, args.port), _Handler)
    log.info("VoxCPM2 server listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
