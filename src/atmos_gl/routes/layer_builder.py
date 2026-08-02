#!/usr/bin/env python3
"""Reprioritise layer_builder's in-memory multi-hour round-robin order (architecture
review candidate "test changed processes much more quickly") -- lets a developer bump
a changed layer to the front of the round instead of waiting through the rest of
TASK_CLASSES' declared order.

layer_builder runs in its own container/process (see docker-compose.yml) and owns the
actual RoundRobinOrder instance -- this route holds no state itself, it only proxies to
layer_builder's internal listener (round_robin_order.py's module docstring / see
layer_builder.py's _start_order_server()) over the agl docker network. That internal
listener is never reachable from outside this app; this route is the one public entry
point for it, same as every other route in this package.
"""
import os
import logging

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("atmos_gl.routes.layer_builder")

router = APIRouter(prefix="/api/layer_builder", tags=["Layer Builder"])

_ORDER_URL = os.getenv("LAYER_BUILDER_ORDER_URL", "http://layer_builder:9100")
_TIMEOUT_S = 3


class PriorityRequest(BaseModel):
    sections: list[str] = Field(..., min_length=1)


@router.get("/priority")
def get_priority():
    try:
        r = requests.get(f"{_ORDER_URL}/priority", timeout=_TIMEOUT_S)
    except requests.RequestException as e:
        logger.error(f"layer_builder order fetch failed: {e}")
        raise HTTPException(status_code=502, detail="layer_builder unreachable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("error"))
    return r.json()


@router.post("/priority")
def set_priority(req: PriorityRequest):
    try:
        r = requests.post(
            f"{_ORDER_URL}/priority", json=req.model_dump(), timeout=_TIMEOUT_S
        )
    except requests.RequestException as e:
        logger.error(f"layer_builder order update failed: {e}")
        raise HTTPException(status_code=502, detail="layer_builder unreachable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("error"))
    return r.json()


@router.post("/priority/reset")
def reset_priority():
    try:
        r = requests.post(f"{_ORDER_URL}/priority/reset", timeout=_TIMEOUT_S)
    except requests.RequestException as e:
        logger.error(f"layer_builder order reset failed: {e}")
        raise HTTPException(status_code=502, detail="layer_builder unreachable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("error"))
    return r.json()
