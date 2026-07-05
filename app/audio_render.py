"""Render preset audio: attention chime + spoken phrase -> one normalized WAV.

Engines:
  say       macOS built-in TTS
  piper     self-hosted neural TTS ('piper' on PATH + an .onnx voice model)
  recorded  parent-recorded phrase from audio/raw/<id>.* (always wins in auto mode)
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from .config import AUDIO_DIR

RAW_DIR = AUDIO_DIR / "raw"
RATE = 22050  # everything normalized to 22.05 kHz mono 16-bit


class RenderError(Exception):
    pass


# ---- WAV helpers (stdlib only) ----------------------------------------

def _write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as w:
        if not (w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == RATE):
            raise RenderError(f"{path} is not {RATE}Hz mono 16-bit")
        data = w.readframes(w.getnframes())
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def _normalize(samples: list[int], peak: float = 0.95) -> list[int]:
    cur = max(1, max(abs(s) for s in samples))
    gain = peak * 32767 / cur
    return [max(-32768, min(32767, int(s * gain))) for s in samples]


def _to_standard_wav(src: Path, dst: Path) -> None:
    """Convert any audio file to 22.05kHz mono 16-bit wav via afconvert or ffmpeg."""
    if shutil.which("afconvert"):
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(src), str(dst)],
            check=True, capture_output=True,
        )
    elif shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16", str(dst)],
            check=True, capture_output=True,
        )
    else:
        raise RenderError("Need afconvert (macOS) or ffmpeg on PATH to convert audio")


# ---- Chime -------------------------------------------------------------

def make_chime() -> list[int]:
    """Two-tone attention ding (G5 -> C6), ~1.1s, designed to cut through."""
    samples: list[int] = []
    for freq, dur in ((784.0, 0.28), (1046.5, 0.55)):
        n = int(RATE * dur)
        for i in range(n):
            t = i / RATE
            env = min(1.0, i / (RATE * 0.01)) * math.exp(-3.0 * t)  # fast attack, decay
            v = math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * freq * 2 * t)
            samples.append(int(28000 * env * v / 1.35))
        samples.extend([0] * int(RATE * 0.05))
    samples.extend([0] * int(RATE * 0.25))  # breath before the phrase
    return samples


# ---- Speech engines -----------------------------------------------------

def default_piper_model() -> str:
    return os.environ.get("HOLLER_PIPER_MODEL", "")


def available_engines() -> list[str]:
    engines = []
    if shutil.which("say"):
        engines.append("say")
    if shutil.which("piper"):
        engines.append("piper")
    return engines


def _speak_say(text: str, voice: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "s.aiff"
        cmd = ["say", "-o", str(aiff)]
        if voice:
            cmd += ["-v", voice]
        subprocess.run(cmd + [text], check=True, capture_output=True)
        _to_standard_wav(aiff, out)


def _speak_piper(text: str, model: str, out: Path) -> None:
    model = model or default_piper_model()
    if not model:
        raise RenderError("piper engine needs a voice model (tts.piper_model)")
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "s.wav"
        subprocess.run(
            ["piper", "--model", model, "--output_file", str(raw)],
            input=text.encode(), check=True, capture_output=True,
        )
        _to_standard_wav(raw, out)


def _recorded_source(preset_id: str) -> Path | None:
    if not RAW_DIR.is_dir():
        return None
    return next(iter(sorted(RAW_DIR.glob(f"{preset_id}.*"))), None)


# ---- Public API ----------------------------------------------------------

def render_preset(
    preset_id: str,
    text: str,
    engine: str = "auto",
    voice: str = "",
    piper_model: str = "",
) -> Path:
    """Render chime + phrase to audio/<id>.wav and return the path.

    In 'auto' mode a parent recording (audio/raw/<id>.*) always beats TTS,
    then whichever TTS engine is installed.
    """
    AUDIO_DIR.mkdir(exist_ok=True)
    out = AUDIO_DIR / f"{preset_id}.wav"
    speech: list[int] = []

    with tempfile.TemporaryDirectory() as td:
        spoken = Path(td) / "spoken.wav"
        recorded = _recorded_source(preset_id)

        if engine == "recorded" or (engine == "auto" and recorded):
            if not recorded:
                raise RenderError(f"no recording found at audio/raw/{preset_id}.*")
            _to_standard_wav(recorded, spoken)
        elif engine == "none" or not text:
            pass  # chime only
        else:
            resolved = engine
            if resolved == "auto":
                installed = available_engines()
                if not installed:
                    raise RenderError("no TTS engine installed (need 'say' or 'piper')")
                resolved = installed[0]
            if resolved == "say":
                _speak_say(text, voice, spoken)
            elif resolved == "piper":
                _speak_piper(text, piper_model, spoken)
            else:
                raise RenderError(f"unknown engine '{engine}'")

        if spoken.exists():
            speech = _normalize(_read_wav(spoken))

    _write_wav(out, _normalize(make_chime()) + speech + [0] * int(RATE * 0.2))
    return out
