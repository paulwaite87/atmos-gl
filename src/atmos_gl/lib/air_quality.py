#!/usr/bin/env python3
"""Shared helpers for the air_quality (PM2.5/PM10/smoke-AOD/SO2) layer, used by both
the collector (collectors/air_quality.py) and the updater (tasks/air_quality.py) --
same "one source of truth for path conventions" role lib/greenhouse_gases.py plays for
the greenhouse_gases layer.

Sourced from Copernicus CAMS's global atmospheric composition forecasts via the CDS
API, confirmed live: dataset cams-global-atmospheric-composition-forecasts, in-file
variable names pm2p5/pm10/aod550 -- see the published spec's issue comments. Absolute
(current conditions) only -- no baseline/anomaly pair for this layer.

SO2 (issue #254) is a 4th variable rendered here unconditionally like the other
three, but its opacity/threshold settings are owned by the `volcanoes` config section
(Volcano Properties), not `air_quality`'s own -- see tasks/air_quality.py's
_SETTINGS_SECTION_OVERRIDE.
"""
import os

VARIABLES = ("pm2_5", "pm10", "aod", "so2")


def camsforecast_cache_path(workdir: str) -> str:
    """Cache path for the current PM2.5+PM10+AOD netCDF (CAMS global atmospheric
    composition forecasts, leadtime_hour=0 -- the nearest-to-now analysis-initialised
    step)."""
    return os.path.join(workdir, "data", "air_quality_cache_camsforecast.nc")
