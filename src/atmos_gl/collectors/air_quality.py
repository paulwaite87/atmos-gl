#!/usr/bin/env python3
"""Copernicus CAMS PM2.5/PM10/smoke-AOD source for the air_quality layer, via the CDS
API (CDSAPI_KEY) -- same submit-then-poll mechanics, credential resolution, and
bounded timeout as CamsGhgForecastCollector (collectors/greenhouse_gases.py), shared
via lib/cds_client.py rather than re-implemented here.

Absolute (current conditions) only -- there is no baseline/anomaly pair for this
layer, unlike greenhouse_gases' CamsGhgForecastCollector/CamsEgg4BaselineCollector
split, so this is a single collector with no settings_section sharing.

Confirmed live against the real Copernicus ADS API this session: dataset
cams-global-atmospheric-composition-forecasts, a single combined request for all three
variables, data_format=netcdf_zip, no per-dataset licence-acceptance friction (unlike
greenhouse_gases' CAMS/EGG4 datasets).
"""
import logging

import cdsapi

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.air_quality import camsforecast_cache_path
from atmos_gl.lib.cds_client import resolve_cds_credentials, retrieve_with_day_fallback

logger = logging.getLogger(__name__)

# Confirmed live against the real CDS API this session -- see the published spec's
# issue comments for the full investigation.
_CAMS_FORECAST_DATASET = "cams-global-atmospheric-composition-forecasts"
_CAMS_FORECAST_TIMEOUT_S = 300
_CAMS_FORECAST_VARS = (
    "particulate_matter_2.5um",
    "particulate_matter_10um",
    "total_aerosol_optical_depth_550nm",
)
# CAMS issues forecast runs at 00Z/12Z, but the newest run isn't always published yet
# when this collector happens to run -- same day-search fallback shape
# CamsGhgForecastCollector uses for its own publish lag.
_CAMS_FORECAST_SEARCH_DAYS = 3


def build_air_quality_request(date_str: str) -> dict:
    """leadtime_hour '0' is the forecast's initial (nearest-to-now, analysis-
    initialised) step -- matches build_cams_forecast_request()'s reasoning for the
    greenhouse_gases layer."""
    return {
        "variable": list(_CAMS_FORECAST_VARS),
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

        retrieve_with_day_fallback(
            client, _CAMS_FORECAST_DATASET, build_air_quality_request, dest,
            _CAMS_FORECAST_TIMEOUT_S, _CAMS_FORECAST_SEARCH_DAYS, "CAMS air quality forecast",
        )
