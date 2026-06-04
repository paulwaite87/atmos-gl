# worldmap/routes/lightning.py
from fastapi import APIRouter, Response
from worldmap.lib.db import Database

router = APIRouter(prefix="/api", tags=["Weather"])

@router.get("/lightning/geojson")
async def get_lightning_geojson(expiry_hours: int = 12):
    db = Database()
    geojson_string = db.get_lightning_as_geojson(expiry_hours)
    return Response(content=geojson_string, media_type="application/json")
