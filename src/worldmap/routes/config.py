#!/usr/bin/env python3
import os
from fastapi import APIRouter, HTTPException
from worldmap.lib.db import Database
from worldmap.db.field_catalog_adapter import FieldCatalogAdapter
from worldmap.lib.config import WorldMapConfig
from datetime import datetime, timezone, timedelta, date

router = APIRouter(prefix="/api", tags=["System Configuration"])

# Forecast SOURCES. Each source provides an independent hourly data set with its own
# model run cadence; the frontend treats them uniformly ("give me source X's hours +
# valid times"). A product belongs to exactly one source. The `primary` source drives
# the master scrubber timeline; layers on any other source reconcile their own nearest
# hour by wall-clock valid_time against that master. Adding a new source = one entry
# here (+ its collector handler), not new special-cases.
#
# This replaces the old GFS-oriented-with-currents-exception model: GFS is simply the
# source that happens to be primary, and RTOFS (currents) is just another source.
SOURCES = {
    "gfs": {
        "primary": True,  # drives the master timeline
        "products": [
            "isobars",
            "precipitation",
            "wind",
            "temperature",
            "ozone",
            "stormwatch",
            "waves",
        ],
    },
    "rtofs": {
        "primary": False,
        "products": ["currents"],
    },
}

# Backwards-compatible alias: the GFS-source products (some call sites referenced this).
SCRUBBER_PRODUCTS = SOURCES["gfs"]["products"]


def _run_epoch_utc(run_date, run_id):
    """Build the f000 valid-time (UTC) from run_date (str 'YYYYMMDD' or a date/
    datetime) and run_id (str/int hour). Tolerant of the catalog column being
    either text or DATE."""
    run_hour = int(run_id)

    if isinstance(run_date, datetime):
        d = run_date.date()
    elif isinstance(run_date, date):
        d = run_date
    else:
        # string like "20260613" (or "2026-06-13" just in case)
        s = str(run_date).strip()
        if "-" in s:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        else:
            d = datetime.strptime(s, "%Y%m%d").date()

    return datetime(
        d.year,
        d.month,
        d.day,
        run_hour,
        0,
        0,
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
          "run_date": "20260613",
          "run_id": "18",
          "run_epoch_utc": "2026-06-13T18:00:00Z",   # valid time of f000
          "fmin": 0, "fmax": 23,
          "hours": [0,1,...,23],
          "max_hour": 23,                             # convenience = fmax
          "valid_times_utc": { "0": "...Z", "1": "...Z", ... }  # per-hour valid time
        }
      }
    """
    try:
        field_catalog_adapter = FieldCatalogAdapter()

        def z(dt):
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        def source_block(products):
            """Build one source's timeline block from whichever of its products actually
            have catalogued DATA (not which layers are toggled on — display state must
            not make the timeline vanish). Intersects hours over the data-present products
            within that source's own freshest run, so model cycles never mix. Returns None
            if the source has no data yet."""
            present = field_catalog_adapter.products_with_data(products)
            if not present:
                return None
            summary = field_catalog_adapter.get_latest_run_hours(products=present)
            if not summary or not summary.get("hours"):
                return None
            epoch = _run_epoch_utc(summary["run_date"], summary["run_id"])
            rdate = summary["run_date"]
            return {
                "run_date": rdate
                if isinstance(rdate, str)
                else rdate.strftime("%Y%m%d"),
                "run_id": summary["run_id"],
                "run_epoch_utc": z(epoch),
                "fmin": summary["fmin"],
                "fmax": summary["fmax"],
                "max_hour": summary["fmax"],
                "hours": summary["hours"],
                "valid_times_utc": {
                    str(h): z(epoch + timedelta(hours=int(h))) for h in summary["hours"]
                },
            }

        # Build every source's block uniformly. `primary` names the source that drives
        # the master scrubber; non-primary sources are reconciled against it by the
        # frontend. The whole timeline is null only if even the primary has no data.
        sources = {}
        primary_name = None
        for name, spec in SOURCES.items():
            block = source_block(spec["products"])
            if block is not None:
                sources[name] = block
            if spec.get("primary"):
                primary_name = name

        if not primary_name or primary_name not in sources:
            # Primary source has no data yet -> no master timeline. (A non-primary source
            # alone can't drive the scrubber.)
            return {"status": "success", "data": None}

        return {
            "status": "success",
            "data": {
                "sources": sources,
                "primary": primary_name,
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

    if "lightning_collector" in data:
        if not owm_key:
            data["lightning_collector"]["enabled"] = False
            data["lightning_collector"]["RULE__missing_openweather_apikey"] = True

    if "common" in data:
        if not maptiler_key:
            data["common"]["RULE__missing_maptiler"] = True

    return {"status": "success", "data": data}


@router.post("/config")
async def update_config(payload: dict):
    worldmap_config = load_config()

    if "shipping_collector" in payload:
        payload["shipping_collector"].pop("RULE__missing_ais", None)
    if "lightning_collector" in payload:
        payload["lightning_collector"].pop("RULE__missing_openweather_apikey", None)
    if "common" in payload:
        payload["common"].pop("RULE__missing_maptiler", None)

    worldmap_config.config = payload
    worldmap_config.save()
    return {"status": "success", "message": "Configuration updated successfully."}
