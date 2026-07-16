#!/usr/bin/env python3
from fastapi import APIRouter, Response, Query, Depends
from atmos_gl.db.fire_adapter import FireAdapter

router = APIRouter(prefix="/api", tags=["Geology"])


def get_fire_adapter() -> FireAdapter:
    return FireAdapter()


@router.get("/fires/geojson")
async def get_fires_geojson(
    min_confidence: str = Query("low"),
    expiry_hours: int = Query(24),
    max_frp: float = Query(5000.0),
    fire_adapter: FireAdapter = Depends(get_fire_adapter),
):
    geojson_string = fire_adapter.get_fires_as_geojson(min_confidence, expiry_hours, max_frp)
    return Response(content=geojson_string, media_type="application/json")
