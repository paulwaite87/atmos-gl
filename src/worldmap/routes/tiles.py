#!/usr/bin/env python3
"""Raster tile endpoints — generic across all tiled layers.

  GET /api/tiles/{layer}/meta             -> {version, available, minzoom, maxzoom, tileSize}
  GET /api/tiles/{layer}/{z}/{x}/{y}.png  -> a 256x256 Web-Mercator tile for that layer

{layer} is any section registered in raster_tiles.SPECS (e.g. "waves"). Tiles are
pre-rendered and published by the layer builder (see worldmap.tiles.raster_tiles); this
route just serves the published version from disk, rendering on demand only beyond the
pre-rendered depth. `version` changes when the data or render settings change, which busts
the frontend tile cache.
"""

import os
import logging

from fastapi import APIRouter, HTTPException, Response, Depends

from worldmap.lib.config import WorldMapConfig
from worldmap.tiles import raster_tiles as rt

logger = logging.getLogger("worldmap.routes.tiles")
router = APIRouter(prefix="/api/tiles", tags=["Raster Tiles"])

MINZOOM = 0


def get_config() -> WorldMapConfig:
    path = os.getenv("CONFIG_PATH", "./config/worldmap.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Configuration unavailable.")
    config = WorldMapConfig(path)
    config.load()
    return config


def _spec(layer: str):
    spec = rt.SPECS.get(layer)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown tile layer '{layer}'")
    return spec


@router.get("/{layer}/meta")
def layer_meta(layer: str, config: WorldMapConfig = Depends(get_config)):
    spec = _spec(layer)
    info = rt.published_info(spec, config)
    return {
        "status": "success",
        "data": {
            "version": info["version"] if info else None,
            "available": info is not None,
            "minzoom": MINZOOM,
            "maxzoom": info.get("maxzoom", rt.ONDEMAND_MAXZOOM)
            if info
            else rt.ONDEMAND_MAXZOOM,
            "tileSize": info.get("tileSize", rt.TILE_PX) if info else rt.TILE_PX,
        },
    }


@router.get("/{layer}/{z}/{x}/{y}.png")
def layer_tile(
    layer: str, z: int, x: int, y: int, config: WorldMapConfig = Depends(get_config)
):
    spec = _spec(layer)
    if z < 0 or z > rt.ONDEMAND_MAXZOOM:
        raise HTTPException(status_code=404, detail="zoom out of range")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(status_code=404, detail="tile out of range")

    try:
        png = rt.serve_tile(spec, config, z, x, y)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"{layer} tile serve failed")
        raise HTTPException(status_code=500, detail=f"Tile failed: {exc}")

    if png is None:
        # Empty/transparent or not-yet-published tile: MapLibre draws nothing.
        raise HTTPException(status_code=404, detail="empty tile")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
