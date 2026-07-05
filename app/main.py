"""Holler — one-tap broadcast to the kid's speaker."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import audio_render
from .caster import BroadcastError, Caster, DRY_RUN
from .config import (
    AUDIO_CONTENT_TYPES,
    CONFIG_PATH,
    ROOT,
    config_to_dict,
    load_config,
    parse_config,
    save_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("holler")

STATIC_DIR = ROOT / "static"
PORT_OVERRIDE = os.environ.get("HOLLER_PORT")  # else taken from the incoming request

cfg = load_config()
caster = Caster(cfg)
app = FastAPI(title="Holler", docs_url=None, redoc_url=None)

if DRY_RUN:
    log.warning("HOLLER_DRY_RUN is set — broadcasts are simulated, nothing will play")


@app.middleware("http")
async def no_stale_cache(request, call_next):
    """Force revalidation so phones pick up UI/audio changes immediately.

    Without this, browsers heuristically cache the static files and keep
    running old JS after an upgrade. ETag 304s keep it cheap on the LAN.
    """
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _apply_config(new_cfg) -> None:
    global cfg
    cfg = new_cfg
    caster.cfg = new_cfg  # same lock, new settings


def _check_pin(x_pin: str | None) -> None:
    if cfg.pin and x_pin != cfg.pin:
        raise HTTPException(status_code=401, detail="Bad PIN")


def _check_admin(x_admin_pin: str | None) -> None:
    if cfg.admin_pin and x_admin_pin != cfg.admin_pin:
        raise HTTPException(status_code=401, detail="Bad admin PIN")


# ---- Parent app ---------------------------------------------------------

@app.get("/api/presets")
def list_presets():
    return {
        "pin_required": bool(cfg.pin),
        "dry_run": DRY_RUN,
        "presets": [
            {"id": p.id, "label": p.label, "emoji": p.emoji, "ready": p.resolve_file() is not None}
            for p in cfg.presets
        ],
    }


@app.get("/api/health")
def health():
    devices = {
        d.name: (DRY_RUN or caster.device_reachable(d)) for d in cfg.enabled_devices()
    }
    return {"ok": True, "speaker_reachable": any(devices.values()), "devices": devices}


@app.post("/api/broadcast/{preset_id}")
async def broadcast(preset_id: str, request: Request, x_pin: str | None = Header(default=None)):
    _check_pin(x_pin)

    preset = cfg.preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Unknown preset")
    audio_file = preset.resolve_file()
    if audio_file is None:
        raise HTTPException(status_code=500, detail=f"No audio rendered for '{preset_id}'")

    host = caster.detect_advertise_host()
    port = int(PORT_OVERRIDE or request.url.port or 80)
    audio_url = f"http://{host}:{port}/audio/{audio_file.name}"
    content_type = AUDIO_CONTENT_TYPES[audio_file.suffix.lower()]

    try:
        results, elapsed = await caster.broadcast(audio_url, content_type)
    except BroadcastError as e:
        if str(e) == "busy":
            raise HTTPException(status_code=429, detail="A broadcast is already playing")
        raise HTTPException(status_code=502, detail=str(e))

    delivered = sum(1 for r in results.values() if r["ok"])
    if delivered == 0:
        log.error("broadcast '%s' failed on all speakers: %s", preset_id, results)
        raise HTTPException(status_code=502, detail="Speaker unreachable or cast failed")

    log.info("broadcast '%s' delivered to %d/%d speakers in %.1fs",
             preset_id, delivered, len(results), elapsed)
    return {
        "ok": True,
        "preset": preset_id,
        "delivered": delivered,
        "total": len(results),
        "results": results,
        "elapsed": round(elapsed, 1),
    }


# ---- Admin --------------------------------------------------------------

@app.get("/api/admin/config")
def admin_get_config(x_admin_pin: str | None = Header(default=None)):
    _check_admin(x_admin_pin)
    return {
        "config": config_to_dict(cfg),
        "engines": audio_render.available_engines(),
        "dry_run": DRY_RUN,
        "presets_status": {p.id: p.resolve_file() is not None for p in cfg.presets},
    }


@app.put("/api/admin/config")
def admin_save_config(raw: dict = Body(...), x_admin_pin: str | None = Header(default=None)):
    _check_admin(x_admin_pin)
    try:
        new_cfg = parse_config(raw)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    removed = {p.id for p in cfg.presets} - {p.id for p in new_cfg.presets}
    save_config(new_cfg, CONFIG_PATH)
    _apply_config(new_cfg)

    for pid in removed:  # tidy up rendered audio for deleted presets
        for ext in AUDIO_CONTENT_TYPES:
            (audio_render.AUDIO_DIR / f"{pid}{ext}").unlink(missing_ok=True)

    log.info("config saved (%d presets)", len(new_cfg.presets))
    return {"ok": True}


@app.post("/api/admin/scan")
async def admin_scan(x_admin_pin: str | None = Header(default=None)):
    _check_admin(x_admin_pin)

    def _scan():
        import pychromecast

        ccs, browser = pychromecast.get_chromecasts(timeout=8)
        devices = [
            {
                "name": c.cast_info.friendly_name,
                "host": c.cast_info.host,
                "uuid": str(c.cast_info.uuid),
                "model": c.cast_info.model_name,
            }
            for c in ccs
        ]
        browser.stop_discovery()
        return devices

    return {"devices": await asyncio.to_thread(_scan)}


@app.post("/api/admin/render")
async def admin_render(body: dict = Body(default={}), x_admin_pin: str | None = Header(default=None)):
    _check_admin(x_admin_pin)
    ids = body.get("ids") or [p.id for p in cfg.presets]
    results = {}
    for pid in ids:
        preset = cfg.preset(pid)
        if preset is None:
            results[pid] = {"ok": False, "error": "unknown preset"}
            continue
        try:
            await asyncio.to_thread(
                audio_render.render_preset,
                preset.id, preset.tts, cfg.tts_engine, cfg.tts_voice, cfg.tts_piper_model,
            )
            results[pid] = {"ok": True}
        except Exception as e:  # noqa: BLE001 - report per-preset, keep going
            log.exception("render failed for %s", pid)
            results[pid] = {"ok": False, "error": str(e)}
    return {"results": results}


# ---- Static -------------------------------------------------------------

app.mount("/audio", StaticFiles(directory=audio_render.AUDIO_DIR), name="audio")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
