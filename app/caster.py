"""Chromecast broadcast: capture volume -> force loud -> cast -> restore.

Fans out to every enabled device in parallel; each speaker gets its own
volume capture/restore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from uuid import UUID

from .config import Config, Device

log = logging.getLogger("holler.caster")

DRY_RUN = os.environ.get("HOLLER_DRY_RUN", "") not in ("", "0", "false")


class BroadcastError(Exception):
    """Raised when a broadcast could not be delivered at all."""


class Caster:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = asyncio.Lock()

    def detect_advertise_host(self) -> str:
        """LAN IP the speakers should use to fetch audio from this service."""
        if self.cfg.advertise_host:
            return self.cfg.advertise_host
        devices = self.cfg.enabled_devices()
        probe = devices[0].host if devices else "8.8.8.8"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 8009))
            return s.getsockname()[0]
        finally:
            s.close()

    @staticmethod
    def device_reachable(device: Device) -> bool:
        try:
            with socket.create_connection((device.host, 8009), timeout=2):
                return True
        except OSError:
            return False

    async def broadcast(self, audio_url: str, content_type: str) -> tuple[dict, float]:
        """Serialized broadcast to all enabled devices.

        Returns ({device_name: {"ok": bool, "error": str?}}, elapsed_seconds).
        Raises BroadcastError("busy") if one is in flight, or with a message
        if no devices are enabled.
        """
        devices = self.cfg.enabled_devices()
        if not devices:
            raise BroadcastError("no speakers enabled — add one in /admin")
        if self._lock.locked():
            raise BroadcastError("busy")
        async with self._lock:
            start = time.monotonic()
            if DRY_RUN:
                log.info("DRY RUN: would broadcast %s to %s",
                         audio_url, [d.name for d in devices])
                await asyncio.sleep(1.0)
                return {d.name: {"ok": True} for d in devices}, time.monotonic() - start
            outcomes = await asyncio.gather(
                *[asyncio.to_thread(self._broadcast_device, d, audio_url, content_type)
                  for d in devices]
            )
            return dict(zip([d.name for d in devices], outcomes)), time.monotonic() - start

    def _broadcast_device(self, device: Device, audio_url: str, content_type: str) -> dict:
        attempts = 1 + max(0, self.cfg.retries)
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                self._broadcast_sync(device, audio_url, content_type)
                return {"ok": True}
            except Exception as e:  # noqa: BLE001 - surfaced per-device
                last_err = e
                log.warning("[%s] broadcast attempt %d/%d failed: %s",
                            device.name, attempt + 1, attempts, e)
        return {"ok": False, "error": str(last_err)}

    def _broadcast_sync(self, device: Device, audio_url: str, content_type: str) -> None:
        import pychromecast  # deferred: import spins up zeroconf machinery

        cfg = self.cfg
        uuid = UUID(device.uuid) if device.uuid else None
        cast = pychromecast.get_chromecast_from_host(
            (device.host, 8009, uuid, None, device.name),
            tries=1,
            timeout=cfg.connect_timeout,
        )
        try:
            cast.wait(timeout=cfg.connect_timeout)
            if cast.status is None:
                raise BroadcastError(f"speaker at {device.host} did not respond")

            prev_volume = cast.status.volume_level
            log.info("[%s] connected (volume %.2f)", device.name, prev_volume)
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
                    log.exception("[%s] failed to restore volume to %.2f",
                                  device.name, prev_volume)
        finally:
            try:
                cast.disconnect(timeout=3)
            except Exception:  # noqa: BLE001
                pass
