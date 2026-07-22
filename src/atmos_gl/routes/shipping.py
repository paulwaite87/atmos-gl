#!/usr/bin/env python3
from fastapi import APIRouter, Response, Depends, Query
from atmos_gl.db.ship_adapter import ShipAdapter
from atmos_gl.lib.shipping import get_vessel_classes_list

# Define router with an overarching prefix and documentation tag
router = APIRouter(prefix="/api", tags=["Shipping & Maritime"])


def get_ship_adapter() -> ShipAdapter:
    return ShipAdapter()


@router.get("/ships/geojson")
async def get_ships_geojson(ship_adapter: ShipAdapter = Depends(get_ship_adapter)):
    geojson_string = ship_adapter.get_fleet_as_geojson()
    return Response(content=geojson_string, media_type="application/json")


@router.get("/ships/{mmsi}/track")
def get_ship_track(
    mmsi: str,
    limit: int = Query(50, ge=5, le=100),
    ship_adapter: ShipAdapter = Depends(get_ship_adapter),
):
    """Historical positions for a single ship, newest first -- backs the hover-only
    track drawn by shipping.js (shipping.view_tracks/track_limit settings). limit's
    bounds match the track_limit slider (FIELD_SPECS), not get_ship_track's own
    default of 100."""
    return {"status": "success", "data": ship_adapter.get_ship_track(mmsi, limit=limit)}


@router.get("/vessel_classes")
def vessel_classes():
    return {
        "status": "success",
        "data": [{"label": c} for c in get_vessel_classes_list()],
    }
