#!/usr/bin/env python3
from fastapi import APIRouter, Response, Depends
from worldmap.db.marker_adapter import MarkerAdapter

router = APIRouter(prefix="/api", tags=["Markers"])


def get_marker_adapter() -> MarkerAdapter:
    return MarkerAdapter()


@router.get("/markers/geojson")
async def get_markers_geojson(marker_adapter: MarkerAdapter = Depends(get_marker_adapter)):
    """All place/feature markers with their current sampled weather, as GeoJSON.
    The frontend markers layer renders directly from this."""
    geojson_string = marker_adapter.get_markers_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
