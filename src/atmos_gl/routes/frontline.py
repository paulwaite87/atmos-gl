#!/usr/bin/env python3
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from atmos_gl.db.frontline_adapter import FrontlineAdapter
from atmos_gl.lib.frontline_changes import CHANGE_WINDOWS_DAYS, changes_feature_collection

router = APIRouter(prefix="/api", tags=["Frontline"])

# (latest, baseline, days) -> response body, each snapshot keyed by id AND DeepState's
# updatedAt: the collector re-fetches an update DeepState edits under the same id, and
# that must not keep serving the old diff. Only a handful of entries are live at once
# (one per window), so this stays tiny.
_changes_cache: dict[tuple, str] = {}
_CHANGES_CACHE_MAX = 16


def get_frontline_adapter() -> FrontlineAdapter:
    return FrontlineAdapter()


@router.get("/frontline/geojson")
async def get_frontline_geojson(
    frontline_adapter: FrontlineAdapter = Depends(get_frontline_adapter),
):
    return Response(content=frontline_adapter.get_latest_geojson(), media_type="application/json")


@router.get("/frontline/changes")
def get_frontline_changes(
    days: int = Query(7),
    frontline_adapter: FrontlineAdapter = Depends(get_frontline_adapter),
):
    """Occupied-area gains/losses between DeepState's latest update and the one
    current `days` before it (lib/frontline_changes.py)."""
    if days not in CHANGE_WINDOWS_DAYS:
        raise HTTPException(status_code=422, detail=f"days must be one of {CHANGE_WINDOWS_DAYS}")
    latest = frontline_adapter.get_snapshot_at()
    baseline = (
        frontline_adapter.get_snapshot_at(latest["created_at"] - timedelta(days=days))
        if latest else None
    )
    def version(snapshot):
        return (snapshot["id"], snapshot["source_updated_at"]) if snapshot else None

    key = (version(latest), version(baseline), days)
    body = _changes_cache.get(key)
    if body is None:
        body = json.dumps(changes_feature_collection(latest, baseline, days))
        if None not in key:
            if len(_changes_cache) >= _CHANGES_CACHE_MAX:
                _changes_cache.clear()
            _changes_cache[key] = body
    return Response(content=body, media_type="application/json")
