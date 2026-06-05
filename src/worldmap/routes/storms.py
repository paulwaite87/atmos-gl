#!/usr/bin/env python3
from fastapi import APIRouter, Response
from worldmap.lib.db import Database

router = APIRouter(prefix="/api", tags=["Weather"])

@router.get("/storms/geojson")
async def get_storms_geojson():
    db = Database()
    geojson_string = db.get_storms_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
