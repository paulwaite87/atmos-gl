#!/usr/bin/env python3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from worldmap.lib.db import Database
from worldmap.lib.config import WorldMapConfig


app = FastAPI(title="WorldMap Configuration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_config():
    config_path = os.getenv("CONFIG_PATH", "./config/worldmap.conf")

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


@app.get("/api/config")
def get_config():
    worldmap_config = load_config()
    flat_data = {}

    for section in worldmap_config.config.sections():
        for option in worldmap_config.config.options(section):
            key = f"{section}__{option}"
            value = worldmap_config.config.get(section, option)

            # Type casting logic parsing ...
            if value.lower() in ["true", "yes", "on"]:
                flat_data[key] = True
            elif value.lower() in ["false", "no", "off"]:
                flat_data[key] = False
            else:
                try:
                    flat_data[key] = float(value) if "." in value else int(value)
                except ValueError:
                    flat_data[key] = value

    # ENFORCEMENT RULE: Check host system for environment variables
    # If the key is missing or empty, force the UI state to reflect it
    ais_key = os.getenv("AIS_API_KEY", "").strip()
    owm_key = os.getenv("OPENWEATHER_API_KEY", "").strip()

    if not ais_key:
        flat_data["shipping_collector__enabled"] = False
        flat_data["RULE__missing_ais"] = True

    if not owm_key:
        flat_data["weather_scanner__enabled"] = False
        flat_data["RULE__missing_weather"] = True

    return {"status": "success", "data": flat_data}


@app.post("/api/config")
async def update_config(payload: dict):
    worldmap_config = load_config()

    for flat_key, val in payload.items():
        # THE FIX: Split strictly at the double underscore
        if "__" in flat_key:
            section, option = flat_key.split("__", 1)
            if not worldmap_config.config.has_section(section):
                worldmap_config.config.add_section(section)

            if isinstance(val, bool):
                worldmap_config.config.set(section, option, "True" if val else "False")
            else:
                worldmap_config.config.set(section, option, str(val))

    worldmap_config.save()

    return {"status": "success", "message": "Configuration updated successfully."}
