# atmos_gl/routes/lightning.py
from fastapi import APIRouter, Response, Depends
from atmos_gl.db.lightning_adapter import LightningAdapter

router = APIRouter(prefix="/api", tags=["Weather"])


def get_lightning_adapter() -> LightningAdapter:
    return LightningAdapter()


@router.get("/lightning/geojson")
async def get_lightning_geojson(
    expiry_hours: int = 12,
    lightning_adapter: LightningAdapter = Depends(get_lightning_adapter),
):
    geojson_string = lightning_adapter.get_lightning_as_geojson(expiry_hours)
    return Response(content=geojson_string, media_type="application/json")
