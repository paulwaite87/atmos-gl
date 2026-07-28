# atmos_gl/routes/lightning.py
from fastapi import APIRouter, Response, Depends
from pydantic import BaseModel

from atmos_gl.db.lightning_adapter import LightningAdapter
from atmos_gl.db.viewport_adapter import ViewportAdapter

router = APIRouter(prefix="/api", tags=["Weather"])


def get_lightning_adapter() -> LightningAdapter:
    return LightningAdapter()


def get_viewport_adapter() -> ViewportAdapter:
    return ViewportAdapter()


@router.get("/lightning/geojson")
async def get_lightning_geojson(
    expiry_hours: int = 12,
    lightning_adapter: LightningAdapter = Depends(get_lightning_adapter),
):
    geojson_string = lightning_adapter.get_lightning_as_geojson(expiry_hours)
    return Response(content=geojson_string, media_type="application/json")


class ViewportUpdate(BaseModel):
    lat: float
    lon: float
    zoom: float | None = None


@router.post("/viewport")
async def post_viewport(
    body: ViewportUpdate,
    viewport_adapter: ViewportAdapter = Depends(get_viewport_adapter),
):
    """Reports the frontend's current map center -- not a lightning-specific concept,
    but its only consumer today is LightningCollector's grid-queue prioritization (see
    ViewportState's docstring for why this needs to be DB-backed, not in-memory)."""
    viewport_adapter.update_viewport(body.lat, body.lon, body.zoom)
    return {"status": "success"}
