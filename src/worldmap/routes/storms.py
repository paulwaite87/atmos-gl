#!/usr/bin/env python3
from fastapi import APIRouter, Response
from worldmap.db.storm_adapter import StormAdapter

router = APIRouter(prefix="/api", tags=["Weather"])


@router.get("/storms/geojson")
async def get_storms_geojson():
    storm_adapter = StormAdapter()
    geojson_string = storm_adapter.get_storms_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
