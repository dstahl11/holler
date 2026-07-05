#!/usr/bin/env python3
"""CLI wrapper around app.audio_render — render preset audio from the terminal.

The /admin page does this same thing with a Save button; use the CLI for batch
re-renders or engines not configured in the app.

Usage:
    python scripts/render_audio.py [--engine auto|say|piper|recorded|none]
                                   [--voice Samantha] [--piper-model path.onnx]
                                   [--only dinner,now]

Recorded parent voice: drop files at audio/raw/<preset-id>.<any-ext> first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio_render import RenderError, render_preset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "say", "piper", "recorded", "none"])
    ap.add_argument("--voice", default="", help="voice name for 'say' (try: say -v '?')")
    ap.add_argument("--piper-model", default="", help="path to piper .onnx model")
    ap.add_argument("--only", default=None, help="comma-separated preset ids")
    args = ap.parse_args()

    presets = yaml.safe_load((ROOT / "presets.yaml").read_text())["presets"]
    if args.only:
        wanted = set(args.only.split(","))
        presets = [p for p in presets if p["id"] in wanted]

    failures = 0
    for p in presets:
        try:
            out = render_preset(p["id"], p.get("tts", ""), args.engine, args.voice, args.piper_model)
            print(f"  ✓ {out.name}")
        except RenderError as e:
            failures += 1
            print(f"  ! {p['id']}: {e}")

    print("\nDone. Files in audio/ — restart the service if it's running.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
