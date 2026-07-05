#!/usr/bin/env python3
from fastapi import APIRouter, Response
from worldmap.db.marker_adapter import MarkerAdapter

router = APIRouter(prefix="/api", tags=["Markers"])


@router.get("/markers/geojson")
async def get_markers_geojson():
    """All place/feature markers with their current sampled weather, as GeoJSON.
    The frontend markers layer renders directly from this."""
    marker_adapter = MarkerAdapter()
    geojson_string = marker_adapter.get_markers_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
