#!/usr/bin/env python3
"""Flight Radar's REST route (issue #215): reads current aircraft state from the
database, the same shape every other event-feed layer in this app already uses
(routes/shipping.py's get_ships_geojson/get_ship_track), replacing the region-keyed
WebSocket-push architecture of docs/adr/0009 (superseded -- see docs/adr/0010).

A GET here also records the caller's current viewport as its 'interest' row
(AircraftAdapter.record_interest) -- the DB-mediated signal AircraftCollector reads
each cycle to prioritize whichever areas have an active viewer. The read request and
the "tell the collector where I'm looking" request are the same request, not two
separate mechanisms."""
from fastapi import APIRouter, Response, Depends, Query

from atmos_gl.db.aircraft_adapter import AircraftAdapter

router = APIRouter(prefix="/api", tags=["Flight Radar"])


def get_aircraft_adapter() -> AircraftAdapter:
    return AircraftAdapter()


@router.get("/flightradar/geojson")
async def get_aircraft_geojson(
    west: float = Query(...),
    south: float = Query(...),
    east: float = Query(...),
    north: float = Query(...),
    viewer_id: str = Query(..., min_length=1, max_length=64),
    aircraft_adapter: AircraftAdapter = Depends(get_aircraft_adapter),
):
    aircraft_adapter.record_interest(viewer_id, west=west, south=south, east=east, north=north)
    geojson_string = aircraft_adapter.get_fleet_as_geojson(west=west, south=south, east=east, north=north)
    return Response(content=geojson_string, media_type="application/json")


@router.get("/flightradar/{hex_id}/track")
def get_aircraft_track(
    hex_id: str,
    limit: int = Query(50, ge=5, le=100),
    aircraft_adapter: AircraftAdapter = Depends(get_aircraft_adapter),
):
    """Historical positions for a single aircraft, newest first -- backs the
    hover-only track drawn by flightradar.js (flightradar.view_tracks/track_limit
    settings). limit's bounds match the track_limit slider (FIELD_SPECS), same shape
    as routes/shipping.py's get_ship_track."""
    return {"status": "success", "data": aircraft_adapter.get_aircraft_track(hex_id, limit=limit)}
