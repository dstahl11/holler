"""Chromecast broadcast: capture volume -> force loud -> cast -> restore."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from uuid import UUID

from .config import Config

log = logging.getLogger("holler.caster")

DRY_RUN = os.environ.get("HOLLER_DRY_RUN", "") not in ("", "0", "false")


class BroadcastError(Exception):
    """Raised when a broadcast could not be delivered."""


class Caster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def detect_advertise_host(self) -> str:
        """LAN IP the speaker should use to fetch audio from this service."""
        if self.cfg.advertise_host:
            return self.cfg.advertise_host
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.cfg.host, 8009))
            return s.getsockname()[0]
        finally:
            s.close()

    def speaker_reachable(self) -> bool:
        try:
            with socket.create_connection((self.cfg.host, 8009), timeout=2):
                return True
        except OSError:
            return False

    async def broadcast(self, audio_url: str, content_type: str) -> float:
        """Serialized broadcast; returns elapsed seconds. Raises BroadcastError."""
        if self._lock.locked():
            raise BroadcastError("busy")
        async with self._lock:
            start = time.monotonic()
            if DRY_RUN:
                log.info("DRY RUN: would broadcast %s", audio_url)
                await asyncio.sleep(1.0)
                return time.monotonic() - start
            attempts = 1 + max(0, self.cfg.retries)
            last_err: Exception | None = None
            for attempt in range(attempts):
                try:
                    await asyncio.to_thread(self._broadcast_sync, audio_url, content_type)
                    return time.monotonic() - start
                except Exception as e:  # noqa: BLE001 - surfaced to the app as failure
                    last_err = e
                    log.warning("broadcast attempt %d/%d failed: %s", attempt + 1, attempts, e)
            raise BroadcastError(str(last_err))

    def _broadcast_sync(self, audio_url: str, content_type: str) -> None:
        import pychromecast  # deferred: import spins up zeroconf machinery

        cfg = self.cfg
        uuid = UUID(cfg.uuid) if cfg.uuid else None
        cast = pychromecast.get_chromecast_from_host(
            (cfg.host, 8009, uuid, None, cfg.friendly_name),
            tries=1,
            timeout=cfg.connect_timeout,
        )
        try:
            cast.wait(timeout=cfg.connect_timeout)
            if cast.status is None:
                raise BroadcastError(f"speaker at {cfg.host} did not respond")

            prev_volume = cast.status.volume_level
            log.info("connected to %s (volume %.2f)", cfg.host, prev_volume)
            try:
                cast.set_volume(cfg.volume)
                time.sleep(0.4)  # let the volume change land before audio starts

                mc = cast.media_controller
                mc.play_media(audio_url, content_type)
                mc.block_until_active(timeout=cfg.connect_timeout)

                deadline = time.monotonic() + cfg.play_timeout
                started = False
                while time.monotonic() < deadline:
                    state = mc.status.player_state if mc.status else "UNKNOWN"
                    if state in ("PLAYING", "BUFFERING"):
                        started = True
                    elif started:
                        break  # finished (IDLE) or app closed
                    time.sleep(0.25)
                if not started:
                    raise BroadcastError("media session never started playing")
            finally:
                try:
                    cast.set_volume(prev_volume)
                except Exception:  # noqa: BLE001
                    log.exception("failed to restore volume to %.2f", prev_volume)
        finally:
            try:
                cast.disconnect(timeout=3)
            except Exception:  # noqa: BLE001
                pass
