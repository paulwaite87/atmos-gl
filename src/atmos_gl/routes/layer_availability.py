#!/usr/bin/env python3
"""GET /api/layer_availability -- public (unauthenticated, same tier as GET /api/config;
see that route's docstring), so both the admin/personal settings pages AND the
anonymous-reachable live globe page can all read it. Wraps
lib.layer_availability.compute_layer_availability -- see that module's docstring for
the full grilled design."""
from fastapi import APIRouter, HTTPException

from atmos_gl.lib.layer_availability import compute_layer_availability
from atmos_gl.routes.config import load_config

router = APIRouter(prefix="/api", tags=["System Configuration"])


@router.get("/layer_availability")
def get_layer_availability():
    try:
        return {"status": "success", "data": compute_layer_availability(load_config())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
