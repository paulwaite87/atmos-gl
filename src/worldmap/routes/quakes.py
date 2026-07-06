#!/usr/bin/env python3
from fastapi import APIRouter, Response, Query, Depends
from worldmap.db.quake_adapter import QuakeAdapter

router = APIRouter(prefix="/api", tags=["Geology"])


def get_quake_adapter() -> QuakeAdapter:
    return QuakeAdapter()


@router.get("/quakes/geojson")
async def get_quakes_geojson(
    min_mag: float = Query(3.5),
    expiry_hours: int = Query(12),
    recent_hours: int = Query(3),
    quake_adapter: QuakeAdapter = Depends(get_quake_adapter),
):
    geojson_string = quake_adapter.get_quakes_as_geojson(min_mag, expiry_hours, recent_hours)
    return Response(content=geojson_string, media_type="application/json")
