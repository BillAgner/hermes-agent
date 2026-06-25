#!/usr/bin/env python
"""VoxCPM TTS wrapper for the Hermes text_to_speech command provider.

Hermes writes the text to {input_path} and expects audio at {output_path}.
This script reads the text, synthesizes with VoxCPM2 using a calm English
male voice description, and writes the WAV to the expected location.

Usage (from Hermes config):
  command: "C:\\Data\\Hermes\\~\\VoxCPM\\.venv\\Scripts\\python.exe
            C:\\Data\\Hermes_0.17.0\\scripts\\voxcpm_tts.py {input_path} {output_path}"
"""
import os
import sys
import time
from pathlib import Path

# ---- Config from env or defaults ----
MODEL_ID = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
VOICE_DESCRIPTION = os.environ.get(
    "VOXCPM_DEFAULT_VOICE_DESCRIPTION", "calm English male voice"
)
CFG_VALUE = float(os.environ.get("VOXCPM_CFG_VALUE", "2.0"))
TIMESTEPS = int(os.environ.get("VOXCPM_TIMESTEPS", "10"))
DENOISER_ID = os.environ.get(
    "VOXCPM_DENOISER_ID", ""  # empty = no denoiser by default for speed
)
LOAD_DENOISER = DENOISER_ID.strip().lower() not in (
    "", "none", "false", "0", "off"
)
OPTIMIZE = os.environ.get("VOXCPM_OPTIMIZE", "0").strip().lower() not in (
    "0", "false", "off"
)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")


def main() -> int:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_path> <output_path>", file=sys.stderr)
        return 1

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        print("Error: input text is empty", file=sys.stderr)
        return 1

    # Prepend voice description (VoxCPM2 voice design convention)
    if VOICE_DESCRIPTION and not text.startswith("("):
        final_text = f"({VOICE_DESCRIPTION}) {text}"
    else:
        final_text = text

    print(
        f"[voxcpm_tts] model={MODEL_ID} voice='{VOICE_DESCRIPTION}' "
        f"text={repr(text[:60])}{'...' if len(text) > 60 else ''}",
        file=sys.stderr,
        flush=True,
    )

    from voxcpm import VoxCPM  # type: ignore
    import soundfile as sf  # type: ignore
    import numpy as np

    t0 = time.time()
    model = VoxCPM.from_pretrained(
        hf_model_id=MODEL_ID,
        load_denoiser=LOAD_DENOISER,
        zipenhancer_model_id=DENOISER_ID if LOAD_DENOISER else None,
        optimize=OPTIMIZE,
        device=os.environ.get("VOXCPM_DEVICE") or None,
        cache_dir=os.environ.get("VOXCPM_CACHE_DIR") or None,
        local_files_only=os.environ.get("VOXCPM_LOCAL_FILES_ONLY", "").lower()
        in ("1", "true", "yes"),
    )
    print(f"[voxcpm_tts] model loaded in {time.time() - t0:.1f}s", file=sys.stderr, flush=True)

    t1 = time.time()
    wav = model.generate(
        text=final_text,
        cfg_value=CFG_VALUE,
        inference_timesteps=TIMESTEPS,
    )
    wav = np.asarray(wav, dtype=np.float32).squeeze()
    sample_rate = int(getattr(model.tts_model, "sample_rate", 48000))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), wav, sample_rate, subtype="PCM_16")

    duration = len(wav) / sample_rate
    print(
        f"[voxcpm_tts] generated {duration:.2f}s in {time.time() - t1:.1f}s → {output_path}",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
