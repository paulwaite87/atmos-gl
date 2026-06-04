#!/usr/bin/env python3
import os
from fastapi import APIRouter, HTTPException
from worldmap.lib.db import Database
from worldmap.lib.config import WorldMapConfig
from worldmap.tasks.common import MapRegion

router = APIRouter(prefix="/api", tags=["System Configuration"])

def load_config():
    config_path = os.getenv("CONFIG_PATH", "./config/worldmap.json")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="Configuration layout unavailable.")
    config = WorldMapConfig(config_path)
    config.load()
    return config


@router.get("/regions")
def get_regions():
    try:
        worldmap_config = load_config()
        current_region = worldmap_config.get_setting("common", "region", "Whole World")

        db = Database()
        regions = db.get_priority_region_list(current_region)
        return {"status": "success", "data": regions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_map_path")
def get_map_path():
    try:
        worldmap_config = load_config()
        target_geometry = worldmap_config.get_setting("common", "target_geometry")
        region = MapRegion(target_geometry=target_geometry)
        return {"status": "success", "data": f"{region.earth_map_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
def get_config():
    worldmap_config = load_config()
    data = worldmap_config.config.copy()

    # Ensure a frontend directive block exists for the shipping UI module
    if "shipping" not in data:
        data["shipping"] = {"enabled": True}

    ais_key = os.getenv("AIS_API_KEY", "").strip()
    owm_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    maptiler_key = os.getenv("MAPTILER_API_KEY", "").strip()

    if "shipping_collector" in data:
        if not ais_key:
            data["shipping_collector"]["enabled"] = False
            data["shipping_collector"]["RULE__missing_ais"] = True

    if "weather_scanner" in data:
        if not owm_key:
            data["weather_scanner"]["enabled"] = False
            data["weather_scanner"]["RULE__missing_weather"] = True

    if "common" in data:
        if not maptiler_key:
            data["common"]["RULE__missing_maptiler"] = True

    return {"status": "success", "data": data}


@router.post("/config")
async def update_config(payload: dict):
    worldmap_config = load_config()

    if "shipping_collector" in payload:
        payload["shipping_collector"].pop("RULE__missing_ais", None)
    if "weather_scanner" in payload:
        payload["weather_scanner"].pop("RULE__missing_weather", None)
    if "common" in payload:
        payload["common"].pop("RULE__missing_maptiler", None)

    worldmap_config.config = payload
    worldmap_config.save()
    return {"status": "success", "message": "Configuration updated successfully."}