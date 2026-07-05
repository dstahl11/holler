"""Load, validate, and save presets.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = Path(os.environ.get("HOLLER_AUDIO_DIR", ROOT / "audio"))
CONFIG_PATH = Path(os.environ.get("HOLLER_CONFIG", ROOT / "presets.yaml"))

AUDIO_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass
class Preset:
    id: str
    label: str
    emoji: str = ""
    tts: str = ""
    file: str = ""  # defaults to <id>.wav / <id>.mp3, whichever exists

    def resolve_file(self) -> Path | None:
        if self.file:
            p = AUDIO_DIR / self.file
            return p if p.is_file() else None
        for ext in AUDIO_CONTENT_TYPES:
            p = AUDIO_DIR / f"{self.id}{ext}"
            if p.is_file():
                return p
        return None


@dataclass
class Config:
    host: str
    uuid: str | None
    friendly_name: str
    volume: float
    connect_timeout: float
    play_timeout: float
    retries: int
    advertise_host: str | None
    pin: str
    admin_pin: str
    tts_engine: str
    tts_voice: str
    tts_piper_model: str
    presets: list[Preset] = field(default_factory=list)

    def preset(self, preset_id: str) -> Preset | None:
        return next((p for p in self.presets if p.id == preset_id), None)


def parse_config(raw: dict) -> Config:
    device = raw.get("device") or {}
    broadcast = raw.get("broadcast") or {}
    security = raw.get("security") or {}
    tts = raw.get("tts") or {}

    presets = []
    seen: set[str] = set()
    for p in raw.get("presets") or []:
        pid = str(p.get("id", "")).strip()
        if not _ID_RE.match(pid):
            raise ValueError(f"bad preset id {pid!r} (lowercase letters/digits/dashes)")
        if pid in seen:
            raise ValueError(f"duplicate preset id {pid!r}")
        seen.add(pid)
        presets.append(
            Preset(
                id=pid,
                label=str(p.get("label") or pid),
                emoji=str(p.get("emoji", "")),
                tts=str(p.get("tts", "")),
                file=str(p.get("file", "")),
            )
        )
    if not presets:
        raise ValueError("config defines no presets")

    return Config(
        host=str(device.get("host", "")),
        uuid=str(device.get("uuid") or "") or None,
        friendly_name=str(device.get("friendly_name", "Speaker")),
        volume=min(1.0, max(0.0, float(broadcast.get("volume", 1.0)))),
        connect_timeout=float(broadcast.get("connect_timeout", 7)),
        play_timeout=float(broadcast.get("play_timeout", 25)),
        retries=int(broadcast.get("retries", 1)),
        advertise_host=str(broadcast.get("advertise_host") or "") or None,
        pin=str(security.get("pin") or ""),
        admin_pin=str(security.get("admin_pin") or ""),
        tts_engine=str(tts.get("engine", "auto")),
        tts_voice=str(tts.get("voice", "")),
        tts_piper_model=str(tts.get("piper_model", "")),
        presets=presets,
    )


def load_config(path: Path = CONFIG_PATH) -> Config:
    return parse_config(yaml.safe_load(path.read_text()))


def config_to_dict(cfg: Config) -> dict:
    return {
        "device": {
            "host": cfg.host,
            "uuid": cfg.uuid or "",
            "friendly_name": cfg.friendly_name,
        },
        "broadcast": {
            "volume": cfg.volume,
            "connect_timeout": cfg.connect_timeout,
            "play_timeout": cfg.play_timeout,
            "retries": cfg.retries,
            "advertise_host": cfg.advertise_host or "",
        },
        "security": {"pin": cfg.pin, "admin_pin": cfg.admin_pin},
        "tts": {
            "engine": cfg.tts_engine,
            "voice": cfg.tts_voice,
            "piper_model": cfg.tts_piper_model,
        },
        "presets": [
            {"id": p.id, "label": p.label, "emoji": p.emoji, "tts": p.tts,
             **({"file": p.file} if p.file else {})}
            for p in cfg.presets
        ],
    }


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Atomic write with a .bak of the previous version.

    Falls back to an in-place write when rename isn't possible — e.g. when the
    config is a single-file Docker bind mount (os.replace gives EBUSY there).
    """
    body = (
        "# Holler configuration — managed by the /admin page (hand-editing is fine too).\n"
        "# Key reference: README.md\n\n"
        + yaml.safe_dump(config_to_dict(cfg), allow_unicode=True, sort_keys=False)
    )
    if path.exists():
        path.with_suffix(".yaml.bak").write_text(path.read_text())
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(body)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        path.write_text(body)
