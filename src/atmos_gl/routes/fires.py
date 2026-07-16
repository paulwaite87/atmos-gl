#!/usr/bin/env python3
import json
import logging

from fastapi import APIRouter, Response, Query, Depends

from atmos_gl.db.fire_adapter import FireAdapter
from atmos_gl.db.field_catalog_adapter import FieldCatalogAdapter
from atmos_gl.lib import fieldstore
from atmos_gl.routes.config import load_config

logger = logging.getLogger("atmos_gl.routes.fires")
router = APIRouter(prefix="/api", tags=["Geology"])


def get_fire_adapter() -> FireAdapter:
    return FireAdapter()


def _sample_nearest(field, lat, lon):
    """Nearest-grid-cell lookup into a fieldstore field's 'values' array at (lat, lon).
    An approximation (not bilinear-interpolated) -- consistent with the precision the
    Fire Weather Index already has (a 0.25 deg GFS grid cell, not point-precise), fine
    for "cut obvious noise" but not a promise of precision."""
    lats, lons, values = field["lat"], field["lon"], field["values"]
    row = int(round((lat - lats[0]) / (lats[1] - lats[0])))
    col = int(round((lon - lons[0]) / (lons[1] - lons[0]))) % len(lons)
    row = max(0, min(len(lats) - 1, row))
    return float(values[row, col])


def _attach_and_filter_by_risk(geojson_string: str, min_risk: float) -> str:
    """Samples the current Fire Weather Index field at every detection's coordinates,
    attaching it as a "fire_risk" property (shown in the frontend popup) and, when
    min_risk > 0, dropping detections below it. Lives here (not FireAdapter, which
    stays DB-only) -- joining a DB row against a fieldstore raster value is a genuinely
    new cross-cutting concern, not a pattern FireAdapter or anything else in the app has
    today. Runs unconditionally (not just when filtering) since the popup wants the
    value for every detection -- so unlike the rest of this route, a fieldstore/DB
    hiccup here must degrade to "no fire_risk attached" rather than 500 the whole
    endpoint (mirrors FireAdapter's own try/except-and-log-degrade methods)."""
    try:
        config = load_config()
        workdir = config.get_setting("common", "workdir", ".")
        store = fieldstore.get_store(workdir, field_catalog_adapter=FieldCatalogAdapter())
        avail = store.field_catalog_adapter.get_latest_run_hours(products=["fire_weather"])
        if not avail or not avail.get("hours"):
            logger.warning("Fires: no fire_weather field available yet; skipping risk lookup.")
            return geojson_string

        field = store.get_field(avail["run_date"], avail["run_id"], avail["hours"][0], "fire_weather")
        if not field or field.get("values") is None:
            return geojson_string

        geojson = json.loads(geojson_string)
        kept = []
        for feature in geojson["features"]:
            lon, lat = feature["geometry"]["coordinates"]
            risk = _sample_nearest(field, lat, lon)
            feature["properties"]["fire_risk"] = risk
            if risk >= min_risk:
                kept.append(feature)
        geojson["features"] = kept
        return json.dumps(geojson)
    except Exception as e:
        logger.error(f"Fires: fire_weather lookup failed, serving detections without fire_risk: {e}")
        return geojson_string


@router.get("/fires/geojson")
async def get_fires_geojson(
    min_confidence: str = Query("low"),
    expiry_hours: int = Query(24),
    max_frp: float = Query(5000.0),
    min_risk: float = Query(0.0),
    fire_adapter: FireAdapter = Depends(get_fire_adapter),
):
    geojson_string = fire_adapter.get_fires_as_geojson(min_confidence, expiry_hours, max_frp)
    geojson_string = _attach_and_filter_by_risk(geojson_string, min_risk)
    return Response(content=geojson_string, media_type="application/json")
