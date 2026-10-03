#!/usr/bin/env python3
from fastapi import APIRouter, Response, Depends
from atmos_gl.db.frontline_adapter import FrontlineAdapter

router = APIRouter(prefix="/api", tags=["Frontline"])


def get_frontline_adapter() -> FrontlineAdapter:
    return FrontlineAdapter()


@router.get("/frontline/geojson")
async def get_frontline_geojson(
    frontline_adapter: FrontlineAdapter = Depends(get_frontline_adapter),
):
    return Response(content=frontline_adapter.get_latest_geojson(), media_type="application/json")
