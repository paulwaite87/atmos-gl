#!/usr/bin/env python3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from worldmap.lib.db import Database
from worldmap.lib.config import WorldMapConfig
from worldmap.tasks.common import MapRegion


app = FastAPI(title="WorldMap Configuration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# This explicitly tells FastAPI to expose the physical "data" directory to the web
app.mount("/data", StaticFiles(directory="data"), name="data")

def load_config():
    config_path = os.getenv("CONFIG_PATH", "./config/worldmap.json")

    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="Configuration layout unavailable.")

    config = WorldMapConfig(config_path)
    config.load()

    return config


@app.get("/api/regions")
def get_regions():
    try:
        # Get current region from config to prioritize it in the list
        worldmap_config = load_config()
        current_region = worldmap_config.get_setting("common", "region", "Whole World")

        db = Database()
        # Returns list of dicts: [{'label': 'NZ', ...}, {'label': 'Europe', ...}]
        regions = db.get_priority_region_list(current_region)

        return {"status": "success", "data": regions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/get_map_path")
def get_map_path():
    try:
        # Get the name of the file for the map of Earth we are using
        worldmap_config = load_config()
        target_geometry =  worldmap_config.get_setting("common", "target_geometry")
        region = MapRegion(target_geometry=target_geometry)
        return {"status": "success", "data": f"{region.earth_map_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
def get_config():
    worldmap_config = load_config()

    # We can pass the native dictionary straight to the frontend
    data = worldmap_config.config.copy()

    # ENFORCEMENT RULE: Check host system for environment variables
    # We inject these rules directly into the relevant dictionary sections
    ais_key = os.getenv("AIS_API_KEY", "").strip()
    owm_key = os.getenv("OPENWEATHER_API_KEY", "").strip()

    if "shipping_collector" in data:
        if not ais_key:
            data["shipping_collector"]["enabled"] = False
            data["shipping_collector"]["RULE__missing_ais"] = True

    if "weather_scanner" in data:
        if not owm_key:
            data["weather_scanner"]["enabled"] = False
            data["weather_scanner"]["RULE__missing_weather"] = True

    return {"status": "success", "data": data}


@app.post("/api/config")
async def update_config(payload: dict):
    worldmap_config = load_config()

    # Strip out the UI-only enforcement rules before saving to disk
    if "shipping_collector" in payload:
        payload["shipping_collector"].pop("RULE__missing_ais", None)
    if "weather_scanner" in payload:
        payload["weather_scanner"].pop("RULE__missing_weather", None)

    # Completely overwrite the in-memory config with the UI payload
    worldmap_config.config = payload
    worldmap_config.save()

    return {"status": "success", "message": "Configuration updated successfully."}