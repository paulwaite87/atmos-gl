#!/usr/bin/env python3
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.templating import Jinja2Templates
from atmos_gl.db.field_catalog_adapter import FieldCatalogAdapter
from atmos_gl.db.region_adapter import RegionAdapter
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.output_files import OUTFILES
from atmos_gl.routes.field_specs import (
    FIELD_SPECS,
    field_label,
    section_label,
    format_slider_badge,
    clamp_slider_value,
    to_display_value,
    initial_color_render,
    is_long_or_url_field,
    is_api_key_field,
    validate_against_specs,
)
from datetime import datetime, timezone, timedelta, date

router = APIRouter(prefix="/api", tags=["System Configuration"])

# Serves the schema-driven config page directly (see the architecture review's "htmx
# for the configuration UI" candidate) -- no /api prefix, since it returns HTML, not JSON.
# The legacy static ui/config/index.html is retired in favour of this route.
ui_router = APIRouter(tags=["Config UI"])

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.globals["field_specs"] = FIELD_SPECS
templates.env.globals["field_label"] = field_label
templates.env.globals["section_label"] = section_label
templates.env.globals["format_slider_badge"] = format_slider_badge
templates.env.globals["clamp_slider_value"] = clamp_slider_value
templates.env.globals["to_display_value"] = to_display_value
templates.env.globals["initial_color_render"] = initial_color_render
templates.env.globals["is_long_or_url_field"] = is_long_or_url_field
templates.env.globals["is_api_key_field"] = is_api_key_field

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
    config_path = os.getenv("CONFIG_PATH", "./config/atmos-gl.json")
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="Configuration layout unavailable.")
    config = AtmosGLConfig(config_path)
    config.load()
    return config


def get_field_catalog_adapter() -> FieldCatalogAdapter:
    return FieldCatalogAdapter()


def get_region_adapter() -> RegionAdapter:
    return RegionAdapter()


@router.get("/forecast_state")
def get_forecast_state(
    field_catalog_adapter: FieldCatalogAdapter = Depends(get_field_catalog_adapter),
):
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
def get_regions(region_adapter: RegionAdapter = Depends(get_region_adapter)):
    try:
        config = load_config()
        current_region = config.get_setting("common", "region", "Whole World")

        regions = region_adapter.get_priority_region_list(current_region)
        return {"status": "success", "data": regions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _build_config_data() -> dict:
    """Load atmos-gl.json and layer in the frontend RULE__ directives (missing-API-key
    warnings, the shipping stub). Shared by the JSON /api/config endpoint and the
    server-rendered /config page so both see identical data."""
    config = load_config()
    data = config.config.copy()

    # Ensure a frontend directive block exists for the shipping UI module
    if "shipping" not in data:
        data["shipping"] = {"enabled": True}

    ais_key = os.getenv("AIS_API_KEY", "").strip()
    owm_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    maptiler_key = os.getenv("MAPTILER_API_KEY", "").strip()
    firms_key = os.getenv("FIRMS_API_KEY", "").strip()

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

    if "fires" in data:
        if not firms_key:
            data["fires"]["enabled"] = False
            data["fires"]["RULE__missing_firms_apikey"] = True

    # Not stored in config.json, not user-editable (see lib/output_files.py) -- injected
    # here so the frontend can still read cfg.outfile exactly as before, just sourced
    # from the same hardcoded value the render task itself uses.
    for section, path in OUTFILES.items():
        data.setdefault(section, {})["outfile"] = path

    return data


@router.get("/config")
def get_config():
    return {"status": "success", "data": _build_config_data()}


@ui_router.get("/config")
def config_page(request: Request):
    return templates.TemplateResponse(
        request, "config.html", {"config_data": _build_config_data()}
    )


@router.post("/config")
async def update_config(payload: dict):
    errors = validate_against_specs(payload)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    config = load_config()

    if "shipping_collector" in payload:
        payload["shipping_collector"].pop("RULE__missing_ais", None)
    if "lightning_collector" in payload:
        payload["lightning_collector"].pop("RULE__missing_openweather_apikey", None)
    if "common" in payload:
        payload["common"].pop("RULE__missing_maptiler", None)
    if "fires" in payload:
        payload["fires"].pop("RULE__missing_firms_apikey", None)

    # outfile is injected read-time-only by _build_config_data() (see OUTFILES/
    # lib/output_files.py) -- never a real stored setting. Strip it the same way the
    # RULE__ flags above are, so a save doesn't persist a client-echoed copy to disk.
    for section in OUTFILES:
        if section in payload:
            payload[section].pop("outfile", None)

    config.config = payload
    config.save()
    return {"status": "success", "message": "Configuration updated successfully."}
