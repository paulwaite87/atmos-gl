#!/usr/bin/env python3
from fastapi import APIRouter, Response, Depends

from terrascope.db.gdacs_adapter import GdacsAdapter

router = APIRouter(prefix="/api", tags=["Hazards"])


def get_gdacs_adapter() -> GdacsAdapter:
    return GdacsAdapter()


@router.get("/gdacs/geojson")
async def get_gdacs_geojson(gdacs_adapter: GdacsAdapter = Depends(get_gdacs_adapter)):
    return Response(
        content=gdacs_adapter.get_events_as_geojson(), media_type="application/json"
    )
