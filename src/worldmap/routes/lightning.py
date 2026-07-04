# worldmap/routes/lightning.py
from fastapi import APIRouter, Response
from worldmap.db.lightning_adapter import LightningAdapter

router = APIRouter(prefix="/api", tags=["Weather"])


@router.get("/lightning/geojson")
async def get_lightning_geojson(expiry_hours: int = 12):
    lightning_adapter = LightningAdapter()
    geojson_string = lightning_adapter.get_lightning_as_geojson(expiry_hours)
    return Response(content=geojson_string, media_type="application/json")
