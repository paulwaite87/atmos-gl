#!/usr/bin/env python3
from fastapi import APIRouter, Response, Query, Depends

from terrascope.db.internet_outages_adapter import InternetOutagesAdapter

router = APIRouter(prefix="/api", tags=["Hazards"])


def get_internet_outages_adapter() -> InternetOutagesAdapter:
    return InternetOutagesAdapter()


@router.get("/internet_outages/countries")
async def get_internet_outages_countries(
    grace_days: float = Query(1),
    adapter: InternetOutagesAdapter = Depends(get_internet_outages_adapter),
):
    """Countries with an ongoing outage, plus ones that ended within grace_days, keyed by
    ISO country code; no geometry -- the frontend joins on the code."""
    return Response(
        content=adapter.get_countries_as_json(grace_days),
        media_type="application/json",
    )
