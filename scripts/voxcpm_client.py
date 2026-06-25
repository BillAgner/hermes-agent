"""Thin client for the persistent VoxCPM2 server.

Called by Hermes TTS as a command provider:
  command: python voxcpm_client.py {input_path} {output_path}

If the server (port 9121) is not running, starts it and waits for it to
become ready before sending the synthesis request.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_HOST = os.environ.get("VOXCPM_SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("VOXCPM_SERVER_PORT", "9121"))
SERVER_URL  = f"http://{SERVER_HOST}:{SERVER_PORT}"
STARTUP_TIMEOUT = 180  # seconds to wait for server model load

VOXCPM_ROOT   = Path(__file__).parent.parent / "~" / "VoxCPM"
VOXCPM_PYTHON = VOXCPM_ROOT / ".venv" / "Scripts" / "python.exe"
SERVER_SCRIPT = Path(__file__).parent / "voxcpm_server.py"

VOICE_DESCRIPTION = os.environ.get("VOXCPM_DEFAULT_VOICE_DESCRIPTION", "calm English male voice")
CFG_VALUE  = float(os.environ.get("VOXCPM_CFG_VALUE", "2.0"))
TIMESTEPS  = int(os.environ.get("VOXCPM_TIMESTEPS", "10"))


def _server_ready() -> bool:
    try:
        resp = urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3)
        data = json.loads(resp.read())
        return data.get("ok") and data.get("model_loaded")
    except Exception:
        return False


def _start_server():
    """Launch the persistent server as a detached background process."""
    print("[voxcpm_client] Starting persistent VoxCPM2 server…", file=sys.stderr, flush=True)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(VOXCPM_PYTHON), str(SERVER_SCRIPT), "--host", SERVER_HOST, "--port", str(SERVER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _wait_for_server():
    """Wait until the server is up and model is loaded."""
    deadline = time.time() + STARTUP_TIMEOUT
    # Give it a moment to start the process
    time.sleep(2)
    while time.time() < deadline:
        if _server_ready():
            print("[voxcpm_client] Server ready", file=sys.stderr, flush=True)
            return
        time.sleep(3)
    raise TimeoutError(f"VoxCPM2 server did not become ready within {STARTUP_TIMEOUT}s")


def main() -> int:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_path> <output_path>", file=sys.stderr)
        return 1

    input_path  = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        print("Error: input text is empty", file=sys.stderr)
        return 1

    # Ensure server is running
    if not _server_ready():
        _start_server()
        _wait_for_server()

    # POST synthesis request
    payload = json.dumps({
        "text": text,
        "voice_description": VOICE_DESCRIPTION,
        "cfg_value": CFG_VALUE,
        "timesteps": TIMESTEPS,
    }).encode()

    t0 = time.time()
    req = urllib.request.Request(
        f"{SERVER_URL}/synthesize",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            wav_bytes = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[voxcpm_client] Server error {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[voxcpm_client] Request failed: {e}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(wav_bytes)
    print(f"[voxcpm_client] Done in {time.time()-t0:.2f}s → {output_path}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
