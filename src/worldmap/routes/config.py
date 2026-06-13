#!/usr/bin/env python3
import os
from fastapi import APIRouter, HTTPException
from worldmap.lib.db import Database
from worldmap.lib.config import WorldMapConfig
from datetime import datetime, timezone, timedelta, date

router = APIRouter(prefix="/api", tags=["System Configuration"])

# The animated layers the scrubber controls. An hour is only offered when every
# one of these (that is enabled) has data for it. Keep in sync with the frontend.
SCRUBBER_PRODUCTS = ["isobars", "precipitation", "wind", "temperature", "ozone", "stormwatch"]

def _run_epoch_utc(gfs_date, gfs_run):
    """Build the f000 valid-time (UTC) from gfs_date (str 'YYYYMMDD' or a date/
    datetime) and gfs_run (str/int hour). Tolerant of the catalog column being
    either text or DATE."""
    run_hour = int(gfs_run)

    if isinstance(gfs_date, datetime):
        d = gfs_date.date()
    elif isinstance(gfs_date, date):
        d = gfs_date
    else:
        # string like "20260613" (or "2026-06-13" just in case)
        s = str(gfs_date).strip()
        if "-" in s:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        else:
            d = datetime.strptime(s, "%Y%m%d").date()

    return datetime(
        d.year, d.month, d.day, run_hour, 0, 0,
        tzinfo=timezone.utc,
    )

def load_config():
    config_path = os.getenv("CONFIG_PATH", "./config/worldmap.json")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="Configuration layout unavailable.")
    config = WorldMapConfig(config_path)
    config.load()
    return config


@router.get("/forecast_state")
def get_forecast_state():
    """Run epoch + available forecast hours for the scrubber.

    Returns:
      {
        "status": "success",
        "data": {
          "gfs_date": "20260613",
          "gfs_run": "18",
          "run_epoch_utc": "2026-06-13T18:00:00Z",   # valid time of f000
          "fmin": 0, "fmax": 23,
          "hours": [0,1,...,23],
          "max_hour": 23,                             # convenience = fmax
          "valid_times_utc": { "0": "...Z", "1": "...Z", ... }  # per-hour valid time
        }
      }
    """
    try:
        cfg = load_config()
        # Only require products that are actually enabled, so a disabled layer
        # doesn't shrink the scrubber's range for everyone else.
        required = []
        for p in SCRUBBER_PRODUCTS:
            section = cfg.config.get(p, {})
            if section.get("animated") and section.get("enabled", True):
                required.append(p)

        db = Database()
        summary = db.get_latest_run_hours(products=required or None)
        if not summary or not summary.get("hours"):
            return {"status": "success", "data": None}

        # Run epoch (valid time of f000) from gfs_date + gfs_run.
        gfs_date = summary["gfs_date"]
        gfs_run = summary["gfs_run"]
        run_epoch = _run_epoch_utc(gfs_date, gfs_run)

        def z(dt):
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        valid_times = {
            str(h): z(run_epoch + timedelta(hours=int(h))) for h in summary["hours"]
        }

        return {
            "status": "success",
            "data": {
                "gfs_date": (gfs_date if isinstance(gfs_date, str) else gfs_date.strftime("%Y%m%d")),
                "gfs_run": gfs_run,
                "run_epoch_utc": z(run_epoch),
                "fmin": summary["fmin"],
                "fmax": summary["fmax"],
                "max_hour": summary["fmax"],
                "hours": summary["hours"],
                "valid_times_utc": valid_times,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
