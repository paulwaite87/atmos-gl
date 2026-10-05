#!/usr/bin/env python3
from fastapi import APIRouter, Depends, HTTPException, Query

from atmos_gl.db.country_stats_adapter import CountryStatsAdapter
from atmos_gl.lib.country_stats import (
    DEFAULT_INDICATOR,
    INDICATORS_BY_ID,
    country_entries,
    max_age_years,
)

router = APIRouter(prefix="/api", tags=["Country Statistics"])


def get_country_stats_adapter() -> CountryStatsAdapter:
    return CountryStatsAdapter()


@router.get("/country_stats")
def get_country_stats(
    indicator: str = Query(DEFAULT_INDICATOR),
    max_age_years_setting: int | None = Query(None, alias="max_age_years", ge=0),
    country_stats_adapter: CountryStatsAdapter = Depends(get_country_stats_adapter),
):
    """One Country Statistics indicator: OWID's metadata plus the catalog's display
    settings, and each country's latest value -- or, when it's more than
    max_age_years behind the newest country, just its year, marked stale. The cutoff
    is applied here, at read time, so changing it needs no re-fetch."""
    spec = INDICATORS_BY_ID.get(indicator)
    if spec is None:
        raise HTTPException(status_code=422, detail=f"indicator must be one of {sorted(INDICATORS_BY_ID)}")
    stored = country_stats_adapter.get_indicator(indicator) or {"metadata": {}, "rows": {}}
    metadata = stored["metadata"]
    countries, newest = country_entries(stored["rows"], max_age_years(spec, max_age_years_setting))
    return {
        "indicator": {
            "id": spec.id,
            "title": metadata.get("title") or spec.label,
            "unit": metadata.get("unit"),
            "short_unit": metadata.get("short_unit"),
            "citation": metadata.get("citation"),
            "last_updated": metadata.get("source_last_updated"),
            "scale": spec.scale,
            "colormap": spec.colormap,
            "domain": list(spec.domain) if spec.domain else None,
        },
        "newest_year": newest,
        "countries": countries,
    }
