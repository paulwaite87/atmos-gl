#!/usr/bin/env python3
"""On-demand viewport rendering.

The scheduled layer builder renders each layer once for the whole world. That single
world PNG is fine zoomed out, but when the user zooms in it's just magnified, so
coastlines and detail go blocky. These routes re-render a layer for the *current map
bounds* at full resolution, so the visible area is always rendered sharp.

The GRIB the scheduled build downloaded is reused from the on-disk cache, so an
on-demand render is only the plot step (no re-download). Results are written to a
content-addressed cache file (keyed on bbox + size + GRIB identity + layer settings)
so revisiting a view is instant and the housekeeper can expire them via the
``<section>_cache_`` prefix.
"""
import os
import glob
import json
import hashlib
import logging
import threading

from fastapi import APIRouter, HTTPException, Query

from worldmap.lib.config import WorldMapConfig
from worldmap.tasks.common import MapData, MapRegion
from worldmap.tasks.waves import WavesUpdater

logger = logging.getLogger("worldmap.routes.render")
router = APIRouter(prefix="/api/render", tags=["On-demand Rendering"])

MERCATOR_LAT = 85.0511          # Web-Mercator latitude limit
MAX_PX = 3072                   # cap per-side render resolution (memory/time guard)

# Heavy matplotlib/cartopy renders are serialised so concurrent viewport requests
# don't thrash the worker; the frontend debounces and drops stale responses anyway.
_render_lock = threading.Lock()
_map_data = None                # cached MapData (its DB connection is reused)


def _load_config():
    path = os.getenv("CONFIG_PATH", "./config/worldmap.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Configuration unavailable.")
    config = WorldMapConfig(path)
    config.load()
    return config


def _get_map_data(config):
    global _map_data
    if _map_data is None:
        _map_data = MapData(config)
    return _map_data


@router.get("/waves")
def render_waves(
    minlon: float = Query(...),
    minlat: float = Query(...),
    maxlon: float = Query(...),
    maxlat: float = Query(...),
    w: int = Query(2048, description="requested render width in px"),
    h: int = Query(1024, description="requested render height in px"),
):
    """Render the waves heat field for a bbox and return the image/key/bounds.

    Response: {status, cached, data: {image, key, bounds}} where image/key are paths
    relative to the static root (load as `${MAP_UI}/${image}`) and bounds is the
    clamped [minlon, minlat, maxlon, maxlat] the image actually covers.
    """
    config = _load_config()
    workdir = config.get_setting("common", "workdir", ".")
    data_dir = os.path.join(workdir, "data")

    # Reuse the most recently cached waves GRIB from the scheduled build.
    gribs = sorted(
        glob.glob(os.path.join(data_dir, "waves_cache_gfs_waves_*.grib2")),
        key=os.path.getmtime,
    )
    if not gribs:
        raise HTTPException(
            status_code=503,
            detail="Waves data not yet available; scheduled render pending.",
        )
    grib_path = gribs[-1]

    # Clamp to the renderable Mercator/world envelope and sane sizes.
    minlat = max(float(minlat), -MERCATOR_LAT)
    maxlat = min(float(maxlat), MERCATOR_LAT)
    minlon = max(float(minlon), -180.0)
    maxlon = min(float(maxlon), 180.0)
    if maxlon <= minlon or maxlat <= minlat:
        # Phase 1 renders a single non-wrapping rectangle; antimeridian-crossing or
        # degenerate views are rejected so the caller can fall back to the world image.
        raise HTTPException(status_code=400, detail="Degenerate or wrapping bbox.")
    w = max(256, min(MAX_PX, int(w)))
    h = max(256, min(MAX_PX, int(h)))
    bbox = [round(minlon, 4), round(minlat, 4), round(maxlon, 4), round(maxlat, 4)]

    # Content-addressed cache: same view + same data + same settings -> same file.
    sig = f"{bbox}|{w}x{h}|{os.path.getmtime(grib_path):.0f}|{config.get_section('waves')}"
    key = hashlib.md5(sig.encode()).hexdigest()[:12]
    image_rel = f"data/waves_cache_view_{key}.png"
    key_rel = f"data/waves_cache_view_{key}_key.png"
    out_path = os.path.join(workdir, image_rel)
    payload = {"image": image_rel, "key": key_rel, "bounds": bbox}

    if os.path.exists(out_path):
        return {"status": "success", "cached": True, "data": payload}

    with _render_lock:
        if not os.path.exists(out_path):       # re-check inside the lock
            try:
                map_data = _get_map_data(config)
                map_data.region = MapRegion(
                    target_geometry=f"{w}x{h}", region=json.dumps(bbox)
                )
                updater = WavesUpdater(config, map_data)
                updater.grib_path = grib_path
                updater.output_path = out_path
                updater.plot()
            except Exception as exc:  # noqa: BLE001 - surface render failures to client
                logger.exception("On-demand waves render failed")
                raise HTTPException(status_code=500, detail=f"Render failed: {exc}")

    return {"status": "success", "cached": False, "data": payload}
