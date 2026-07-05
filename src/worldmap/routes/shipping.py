#!/usr/bin/env python3
from fastapi import APIRouter, Response
from worldmap.db.ship_adapter import ShipAdapter
from worldmap.lib.shipping import get_vessel_classes_list

# Define router with an overarching prefix and documentation tag
router = APIRouter(prefix="/api", tags=["Shipping & Maritime"])


@router.get("/ships/geojson")
async def get_ships_geojson():
    ship_adapter = ShipAdapter()
    geojson_string = ship_adapter.get_fleet_as_geojson()
    return Response(content=geojson_string, media_type="application/json")


@router.get("/vessel_classes")
def vessel_classes():
    return {
        "status": "success",
        "data": [{"label": c} for c in get_vessel_classes_list()],
    }
