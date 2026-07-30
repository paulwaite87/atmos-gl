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
three, owned by `air_quality`'s own opacity/threshold settings same as PM2.5/PM10/AOD.

so2_volcanic is a 5th, separate variable: CDS's dataset also exposes a
volcanic-specific total-column SO2 field (in-file "tc_VSO2", a different quantity
from so2's general "tcso2" -- it isolates ash-plume-associated SO2 rather than all
atmospheric SO2 from every source). Confirmed live it shares so2's rough order of
magnitude (both reuse the same 1.0/20 DU default-min/ceiling -- see
tasks/air_quality.py's _DEFAULT_MIN comment), but it's far more bursty/skewed --
heavy-tailed toward real eruption events rather than smoothly varying background, so
a quiet-activity forecast run can look almost entirely empty while an active one
spikes hard in the plume region. It backs Volcano Properties' "Smoke Plume" overlay
exclusively -- not user-selectable as an Air Quality Variable option -- so its
opacity/threshold settings are owned by the `volcanoes` config section instead of
`air_quality`'s own; see tasks/air_quality.py's _SETTINGS_SECTION_OVERRIDE.
"""
import os

VARIABLES = ("pm2_5", "pm10", "aod", "so2", "so2_volcanic")


def camsforecast_cache_path(workdir: str) -> str:
    """Cache path for the current PM2.5+PM10+AOD netCDF (CAMS global atmospheric
    composition forecasts, leadtime_hour=0 -- the nearest-to-now analysis-initialised
    step)."""
    return os.path.join(workdir, "data", "air_quality_cache_camsforecast.nc")
