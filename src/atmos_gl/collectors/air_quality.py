#!/usr/bin/env python3
"""Copernicus CAMS PM2.5/PM10/smoke-AOD/SO2(x2) source for the air_quality layer, via
the CDS API (CDSAPI_KEY) -- same submit-then-poll mechanics, credential resolution, and
bounded timeout as CamsGhgForecastCollector (collectors/greenhouse_gases.py), shared
via lib/cds_client.py rather than re-implemented here.

Absolute (current conditions) only -- there is no baseline/anomaly pair for this
layer, unlike greenhouse_gases' CamsGhgForecastCollector/CamsEgg4BaselineCollector
split, so this is a single collector with no settings_section sharing.

Confirmed live against the real Copernicus ADS API this session: dataset
cams-global-atmospheric-composition-forecasts, a single combined request for all
variables, data_format=netcdf_zip, no per-dataset licence-acceptance friction (unlike
greenhouse_gases' CAMS/EGG4 datasets). Unlike greenhouse_gases' dataset (one run/day,
date-only search), this dataset's request schema REQUIRES `type: ["forecast"]` and a
`time` field selecting which of the day's two runs (00:00/12:00 UTC) -- confirmed via
the dataset's live /constraints endpoint after a plain date-only request (mirroring
greenhouse_gases' shape) 400'd with "Request has not produced a valid combination of
values". `pressure_level`/`model_level` appear in the dataset's input schema as
declared fields but must be OMITTED entirely for these single-level variables (passing
them, even as empty lists, was not needed -- the live-confirmed working request has
neither key at all).

SO2 (issue #254, the Volcano Properties "Smoke Plume" overlay) added as a 4th
variable in the same combined request -- `total_column_sulphur_dioxide`, the
vertically-integrated column quantity (confirmed live against the dataset's own
form schema), not `sulphur_dioxide` (the 3D near-surface mixing ratio). A 5th,
`total_column_volcanic_sulphur_dioxide`, added alongside it (not instead of) -- CDS's
own schema exposes this as a genuinely distinct variable, not a filtered view of the
general one, isolating ash-plume-associated SO2 specifically rather than all
atmospheric SO2 from every source (industrial, general atmospheric, etc.). Both
confirmed live against real downloaded files -- general SO2 as in-file "tcso2",
volcanic as in-file "tc_VSO2" (see tasks/air_quality.py's _CAMS_VARS). general SO2
stays a selectable Air Quality Variable option; volcanic SO2 backs Smoke Plume
exclusively (see tasks/air_quality.py's _SETTINGS_SECTION_OVERRIDE). Both fetched
unconditionally regardless of whether Air Quality or Volcanoes is the layer currently
enabled -- this collector has no knowledge of either layer's settings, same as before.
"""
import logging
from datetime import datetime, timedelta, timezone

import cdsapi

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.air_quality import camsforecast_cache_path
from atmos_gl.lib.cds_client import resolve_cds_credentials, retrieve_with_fallback

logger = logging.getLogger(__name__)

# Confirmed live against the real CDS API this session -- see the published spec's
# issue comments for the full investigation.
_CAMS_FORECAST_DATASET = "cams-global-atmospheric-composition-forecasts"
_CAMS_FORECAST_TIMEOUT_S = 300
_CAMS_FORECAST_VARS = (
    "particulate_matter_2.5um",
    "particulate_matter_10um",
    "total_aerosol_optical_depth_550nm",
    "total_column_sulphur_dioxide",
    "total_column_volcanic_sulphur_dioxide",
)
# CAMS issues two atmospheric-composition forecast runs per day (00Z/12Z), unlike
# greenhouse_gases' one-run/day dataset -- the newest run isn't always published yet
# when this collector happens to run, so candidates search backward across BOTH axes
# (day, then time-of-day within each day), newest first. 3 days x 2 times/day gives the
# same ~72h worst-case lookback margin as greenhouse_gases' 3-day search.
_CAMS_FORECAST_SEARCH_DAYS = 3
_CAMS_RUN_TIMES = ("12:00", "00:00")  # newest-within-a-day first


def build_air_quality_request(date_str: str, run_time: str) -> dict:
    """leadtime_hour '0' is the forecast's initial (nearest-to-now, analysis-
    initialised) step -- matches build_cams_forecast_request()'s reasoning for the
    greenhouse_gases layer. `type`/`time` are both required by this dataset's request
    schema (unlike greenhouse_gases' dataset, which has neither) -- confirmed live."""
    return {
        "variable": list(_CAMS_FORECAST_VARS),
        "type": ["forecast"],
        "time": [run_time],
        "leadtime_hour": ["0"],
        "date": f"{date_str}/{date_str}",
        "data_format": "netcdf_zip",
    }


class AirQualityCollector(CollectorBase):
    """Current-conditions source: CAMS global atmospheric composition forecasts (PM2.5,
    PM10, smoke/AOD) via the CDS API. Runs a normal periodic cadence, matching
    CamsGhgForecastCollector, so data_status() stays the plain CollectorBase
    freshness-decay default."""

    section = "air_quality"
    channel_key = "air_quality"
    datasource_key = "cams_ads"
    display_label = "CAMS Air Quality Forecast"

    def collect(self) -> None:
        creds = resolve_cds_credentials(self.datasource_url, "CAMS air quality forecast")
        if creds is None:
            return
        base_url, api_key = creds

        dest = camsforecast_cache_path(self.workdir)
        client = cdsapi.Client(url=base_url, key=api_key)

        requests = [
            build_air_quality_request(
                (datetime.now(timezone.utc) - timedelta(days=day_offset)).strftime("%Y-%m-%d"),
                run_time,
            )
            for day_offset in range(_CAMS_FORECAST_SEARCH_DAYS)
            for run_time in _CAMS_RUN_TIMES
        ]
        retrieve_with_fallback(
            client, _CAMS_FORECAST_DATASET, requests, dest,
            _CAMS_FORECAST_TIMEOUT_S, "CAMS air quality forecast",
        )
