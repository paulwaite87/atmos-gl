#!/usr/bin/env python3
import os
import configparser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="WorldMap Double Underscore API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_PATH = "/opt/project/config/worldmap.conf"


def load_raw_config():
    if not os.path.exists(CONFIG_PATH):
        raise HTTPException(status_code=404, detail="Configuration layout unavailable.")
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    return config


@app.get("/api/config")
def get_config():
    config = load_raw_config()
    flat_data = {}

    for section in config.sections():
        for option in config.options(section):
            key = f"{section}__{option}"
            value = config.get(section, option)

            # Type casting logic parsing ...
            if value.lower() in ['true', 'yes', 'on']:
                flat_data[key] = True
            elif value.lower() in ['false', 'no', 'off']:
                flat_data[key] = False
            else:
                try:
                    flat_data[key] = float(value) if '.' in value else int(value)
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
    config = load_raw_config()

    for flat_key, val in payload.items():
        # THE FIX: Split strictly at the double underscore
        if "__" in flat_key:
            section, option = flat_key.split("__", 1)
            if not config.has_section(section):
                config.add_section(section)

            if isinstance(val, bool):
                config.set(section, option, "True" if val else "False")
            else:
                config.set(section, option, str(val))

    with open(CONFIG_PATH, "w") as config_file:
        config.write(config_file)

    return {"status": "success", "message": "Configuration updated successfully."}