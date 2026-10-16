#!/usr/bin/env python3
from fastapi import APIRouter, Response, Query, Depends

from terrascope.db.gdacs_adapter import GdacsAdapter

router = APIRouter(prefix="/api", tags=["Hazards"])


def get_gdacs_adapter() -> GdacsAdapter:
    return GdacsAdapter()


@router.get("/gdacs/geojson")
async def get_gdacs_geojson(
    grace_days: int = Query(7),
    gdacs_adapter: GdacsAdapter = Depends(get_gdacs_adapter),
):
    return Response(
        content=gdacs_adapter.get_events_as_geojson(grace_days),
        media_type="application/json",
    )


@router.get("/gdacs/{event_type}/{event_id}/footprint")
async def get_gdacs_footprint(
    event_type: str,
    event_id: int,
    gdacs_adapter: GdacsAdapter = Depends(get_gdacs_adapter),
):
    return Response(
        content=gdacs_adapter.get_footprint_as_geojson(event_type, event_id),
        media_type="application/json",
    )
