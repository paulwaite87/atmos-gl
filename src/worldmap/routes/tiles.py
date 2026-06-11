#!/usr/bin/env python3
"""Raster tile endpoints for the waves layer.

  GET /api/tiles/waves/meta              -> {version, available, minzoom, maxzoom, tileSize}
  GET /api/tiles/waves/{z}/{x}/{y}.png   -> a 256x256 Web-Mercator wave-height tile

Tiles are pre-rendered and published by the layer builder (see
worldmap.tiles.waves_tiles); this route just serves the published version from disk,
rendering on demand only beyond the pre-rendered depth. `version` changes when the
data or render settings change, which busts the frontend tile cache.
"""
import os
import logging

from fastapi import APIRouter, HTTPException, Response

from worldmap.lib.config import WorldMapConfig
from worldmap.tiles import waves_tiles as wt

logger = logging.getLogger("worldmap.routes.tiles")
router = APIRouter(prefix="/api/tiles", tags=["Raster Tiles"])

MINZOOM = 0


def _load_config():
    path = os.getenv("CONFIG_PATH", "./config/worldmap.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Configuration unavailable.")
    config = WorldMapConfig(path)
    config.load()
    return config


@router.get("/waves/meta")
def waves_meta():
    config = _load_config()
    info = wt.published_info(config)
    return {
        "status": "success",
        "data": {
            "version": info["version"] if info else None,
            "available": info is not None,
            "minzoom": MINZOOM,
            "maxzoom": info.get("maxzoom", wt.ONDEMAND_MAXZOOM) if info else wt.ONDEMAND_MAXZOOM,
            "tileSize": info.get("tileSize", wt.TILE_PX) if info else wt.TILE_PX,
        },
    }


@router.get("/waves/{z}/{x}/{y}.png")
def waves_tile(z: int, x: int, y: int):
    if z < 0 or z > wt.ONDEMAND_MAXZOOM:
        raise HTTPException(status_code=404, detail="zoom out of range")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(status_code=404, detail="tile out of range")

    config = _load_config()
    try:
        png = wt.serve_tile(config, z, x, y)
    except Exception as exc:  # noqa: BLE001
        logger.exception("waves tile serve failed")
        raise HTTPException(status_code=500, detail=f"Tile failed: {exc}")

    if png is None:
        # Empty/transparent or not-yet-published tile: MapLibre draws nothing.
        raise HTTPException(status_code=404, detail="empty tile")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
