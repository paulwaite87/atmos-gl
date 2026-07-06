#!/usr/bin/env python3
from fastapi import APIRouter, Response, Depends
from worldmap.db.storm_adapter import StormAdapter

router = APIRouter(prefix="/api", tags=["Weather"])


def get_storm_adapter() -> StormAdapter:
    return StormAdapter()


@router.get("/storms/geojson")
async def get_storms_geojson(storm_adapter: StormAdapter = Depends(get_storm_adapter)):
    geojson_string = storm_adapter.get_storms_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
