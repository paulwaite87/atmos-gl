#!/usr/bin/env python3
from fastapi import APIRouter, Response
from worldmap.lib.db import Database

router = APIRouter(prefix="/api", tags=["Markers"])


@router.get("/markers/geojson")
async def get_markers_geojson():
    """All place/feature markers with their current sampled weather, as GeoJSON.
    The frontend markers layer renders directly from this."""
    db = Database()
    geojson_string = db.get_markers_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
