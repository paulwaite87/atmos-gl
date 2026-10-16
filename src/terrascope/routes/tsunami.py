#!/usr/bin/env python3
from fastapi import APIRouter, Response, Query, Depends

from terrascope.db.tsunami_adapter import TsunamiAdapter

router = APIRouter(prefix="/api", tags=["Hazards"])


def get_tsunami_adapter() -> TsunamiAdapter:
    return TsunamiAdapter()


@router.get("/tsunami/geojson")
async def get_tsunami_geojson(
    grace_days: float = Query(1),
    tsunami_adapter: TsunamiAdapter = Depends(get_tsunami_adapter),
):
    return Response(
        content=tsunami_adapter.get_events_as_geojson(grace_days),
        media_type="application/json",
    )


@router.get("/tsunami/{centre}/{event_id}/footprint")
async def get_tsunami_footprint(
    centre: str,
    event_id: str,
    tsunami_adapter: TsunamiAdapter = Depends(get_tsunami_adapter),
):
    return Response(
        content=tsunami_adapter.get_footprint_as_geojson(centre, event_id),
        media_type="application/json",
    )
