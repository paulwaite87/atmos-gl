#!/usr/bin/env python3
"""Copernicus CAMS CO2/CH4 sources for the greenhouse_gases layer, both via the CDS
API (CDSAPI_KEY):

  CamsGhgForecastCollector  -- Absolute mode's current-conditions source (CAMS global
                               greenhouse gas forecasts). Runs on a normal periodic
                               cadence (CAMS issues one forecast per day).
  CamsEgg4BaselineCollector -- Anomaly mode's historical-baseline source (CAMS EGG4
                               reanalysis). Rare, on-demand: fetches once per
                               configured baseline_year, then is done -- a past
                               reanalysis year's data never changes once published.

NASA GEOS-CF was the original Absolute-mode choice, but live testing against its real
OPeNDAP server found it does not serve CO2 at all (only CH4) -- see the published
spec's issue comments for the correction. Both collectors now share one data source
family (and one CDSAPI_KEY), submit-then-poll fetch mechanics, and a bounded timeout
so a slow/queued job never stalls collect_once()'s single-threaded synchronous loop
(which every other collector -- GFS, quakes, storms, ... -- also runs through)
indefinitely. Both share the greenhouse_gases config section (species/mode/
baseline_year/per-species scale settings all live there for the frontend) via
settings_section, while keeping independent Data Status rows via their own `section`.

Unlike SST, no upstream source ships a pre-computed CO2/CH4 anomaly -- the
greenhouse_gases updater (tasks/greenhouse_gases.py) computes it itself from these two
collectors' cached fields via lib/greenhouse_gases.compute_anomaly().
"""
import concurrent.futures
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

import cdsapi

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.data_status import build_status, read_process_status
from atmos_gl.lib.greenhouse_gases import (
    camsforecast_cache_path,
    egg4_baseline_cache_path,
    resolve_baseline_year,
)

logger = logging.getLogger(__name__)


def _resolve_cds_credentials(datasource_url_fn, label: str):
    """(base_url, api_key) for a CDS API request, or None (having logged why) if
    either CDSAPI_KEY or the cams_ads datasource isn't configured. Shared by both
    CDS-backed collectors so neither duplicates this check."""
    api_key = os.environ.get("CDSAPI_KEY", "").strip()
    if not api_key:
        logger.warning(f"{label}: no CDSAPI_KEY configured; skipping.")
        return None
    base_url = datasource_url_fn("cams_ads")
    if not base_url:
        logger.warning(f"{label}: no 'cams_ads' datasource configured; skipping.")
        return None
    return base_url, api_key


def _retrieve_with_timeout(client, dataset: str, request: dict, target: str, timeout_s: float):
    """Run client.retrieve() (cdsapi's own blocking submit-then-poll-then-download) in
    a worker thread, bounded by timeout_s. Raises concurrent.futures.TimeoutError if
    the job doesn't finish in time -- the calling thread stops waiting, but the
    worker thread (and the in-flight CDS job) is not cancelled; a future cycle's
    collect() will find the cache still missing and request again."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.retrieve, dataset, request, target)
        future.result(timeout=timeout_s)


def _retrieve_and_unzip(
    client, dataset: str, request: dict, cache_dest: str, timeout_s: float, label: str
):
    """Submit `request` (bounded by timeout_s), then unzip the delivered
    data_format=netcdf_zip archive and move its single .nc member to cache_dest.
    Raises concurrent.futures.TimeoutError (bounded-timeout) or RuntimeError (archive
    had no .nc member) -- callers decide how to log/handle each."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = os.path.join(tmp_dir, "download.zip")
        _retrieve_with_timeout(client, dataset, request, zip_path, timeout_s)

        with zipfile.ZipFile(zip_path) as zf:
            nc_names = [n for n in zf.namelist() if n.endswith(".nc")]
            if not nc_names:
                raise RuntimeError(f"{label}: no .nc file in downloaded archive")
            extracted_path = zf.extract(nc_names[0], tmp_dir)

        os.makedirs(os.path.dirname(cache_dest), exist_ok=True)
        tmp_dest = f"{cache_dest}.tmp"
        shutil.move(extracted_path, tmp_dest)
        os.replace(tmp_dest, cache_dest)


# Absolute mode's current-conditions source. Confirmed live against the real CDS API
# (the request below reached the license-acceptance stage, not a schema error) -- see
# the published spec's issue comments for the full investigation.
_CAMS_FORECAST_DATASET = "cams-global-greenhouse-gas-forecasts"
_CAMS_FORECAST_TIMEOUT_S = 300
_CAMS_FORECAST_VARS = ("co2_column_mean_molar_fraction", "ch4_column_mean_molar_fraction")
# CAMS issues one forecast run per day, but "today"'s run isn't always published yet
# when this collector happens to run (confirmed live: requesting today's date can 400
# while yesterday's succeeds) -- search a few days back for the newest published run,
# same fallback shape resolve_gfs_baseline() (lib/gfs.py) uses for GFS's own publish
# lag.
_CAMS_FORECAST_SEARCH_DAYS = 3


def build_cams_forecast_request(date_str: str) -> dict:
    """leadtime_hour '0' is the forecast's initial (nearest-to-now, analysis-
    initialised) step -- CAMS's own greenhouse gas analysis isn't published close to
    real time, so the forecast's zeroth hour is the freshest available reading."""
    return {
        "variable": list(_CAMS_FORECAST_VARS),
        "leadtime_hour": ["0"],
        "date": f"{date_str}/{date_str}",
        "data_format": "netcdf_zip",
    }


class CamsGhgForecastCollector(CollectorBase):
    """Absolute mode's current-conditions source: CAMS global greenhouse gas
    forecasts via the CDS API. Runs a normal periodic cadence (CAMS issues one
    forecast per day), unlike CamsEgg4BaselineCollector's fetch-once-per-year, so
    data_status() stays the plain CollectorBase freshness-decay default."""

    section = "ghg_forecast"
    settings_section = "greenhouse_gases"
    channel_key = "ghg_forecast"
    datasource_key = "cams_ads"
    display_label = "CAMS Greenhouse Gas Forecast"

    def collect(self) -> None:
        creds = _resolve_cds_credentials(self.datasource_url, "CAMS GHG forecast")
        if creds is None:
            return
        base_url, api_key = creds

        dest = camsforecast_cache_path(self.workdir)
        client = cdsapi.Client(url=base_url, key=api_key)

        last_error = None
        for day_offset in range(_CAMS_FORECAST_SEARCH_DAYS):
            date_str = (
                datetime.now(timezone.utc) - timedelta(days=day_offset)
            ).strftime("%Y-%m-%d")
            request = build_cams_forecast_request(date_str)

            try:
                _retrieve_and_unzip(
                    client, _CAMS_FORECAST_DATASET, request, dest,
                    _CAMS_FORECAST_TIMEOUT_S, "CAMS GHG forecast",
                )
                logger.info(f"CAMS GHG forecast: cached {date_str} -> {os.path.basename(dest)}")
                return
            except concurrent.futures.TimeoutError:
                # A queued (not immediately rejected) job -- today's run does exist,
                # it's just slow. Don't also hammer earlier dates while it's pending.
                logger.warning(
                    f"CAMS GHG forecast: request for {date_str} timed out after "
                    f"{_CAMS_FORECAST_TIMEOUT_S}s; will retry next cycle."
                )
                return
            except Exception as e:
                last_error = e
                logger.debug(
                    f"CAMS GHG forecast: {date_str} not available yet ({e}); "
                    f"trying an earlier date."
                )

        logger.error(
            f"CAMS GHG forecast: no run available in the last "
            f"{_CAMS_FORECAST_SEARCH_DAYS} day(s): {last_error}"
        )


# Anomaly mode's baseline source: CAMS EGG4 reanalysis, 2003-2020 only. Confirmed live
# against the real CDS API -- see the published spec's issue comments.
_EGG4_DATASET = "cams-global-ghg-reanalysis-egg4"
_EGG4_TIMEOUT_S = 300
_EGG4_VARS = ("co2_column_mean_molar_fraction", "ch4_column_mean_molar_fraction")
_EGG4_STEPS = ("0", "3", "6", "9", "12", "15", "18", "21")  # hour-of-day, full daily cycle


def build_egg4_request(year: int) -> dict:
    """CDS API request for CAMS EGG4's CO2+CH4 fields across a full baseline year at
    3-hourly cadence -- GhgUpdater averages across time into a single annual-mean
    baseline field (a fixed single day would introduce a seasonal bias, e.g. always
    comparing "today" against the baseline year's January 1st regardless of the
    current date)."""
    return {
        "variable": list(_EGG4_VARS),
        "date": f"{year}-01-01/{year}-12-31",
        "step": list(_EGG4_STEPS),
        "data_format": "netcdf_zip",
    }


class CamsEgg4BaselineCollector(CollectorBase):
    """Anomaly mode's historical-baseline source: Copernicus CAMS EGG4 reanalysis via
    the CDS API. Fetches once per configured baseline_year and caches it -- a past
    reanalysis year's data never changes once published, so there is nothing to
    re-fetch once cached."""

    section = "ghg_egg4_baseline"
    settings_section = "greenhouse_gases"
    channel_key = "ghg_egg4_baseline"
    datasource_key = "cams_ads"
    display_label = "CAMS EGG4 Baseline"

    def collect(self) -> None:
        year = resolve_baseline_year(self.settings)
        dest = egg4_baseline_cache_path(self.workdir, year)
        if os.path.exists(dest):
            logger.debug(f"CAMS EGG4 baseline: {year} already cached; skipping.")
            return

        creds = _resolve_cds_credentials(self.datasource_url, "CAMS EGG4 baseline")
        if creds is None:
            return
        base_url, api_key = creds

        client = cdsapi.Client(url=base_url, key=api_key)
        request = build_egg4_request(year)

        try:
            _retrieve_and_unzip(
                client, _EGG4_DATASET, request, dest, _EGG4_TIMEOUT_S,
                "CAMS EGG4 baseline",
            )
        except concurrent.futures.TimeoutError:
            logger.warning(
                f"CAMS EGG4 baseline: request for {year} timed out after "
                f"{_EGG4_TIMEOUT_S}s; will retry next cycle."
            )
            return
        except Exception as e:
            logger.error(f"CAMS EGG4 baseline: request for {year} failed: {e}")
            return

        logger.info(f"CAMS EGG4 baseline: cached {year} -> {os.path.basename(dest)}")

    def data_status(self) -> dict:
        """Coverage-based, not time-decay: this collector fetches once per configured
        baseline_year then is done, so a decaying-freshness formula (the CollectorBase
        default) would show perpetual staleness for a source working exactly as
        designed. Mirrors FieldCollectorBase.data_status()'s same reasoning, applied
        to "is the configured year cached" instead of "forecast hour coverage"."""
        year = resolve_baseline_year(self.settings)
        cached = os.path.exists(egg4_baseline_cache_path(self.workdir, year))
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        detail = last_error or (
            f"baseline year {year} cached" if cached else f"fetching baseline year {year}"
        )
        return build_status(
            name=self.section,
            kind="collector",
            percent=100.0 if cached else 0.0,
            last_updated=last_updated,
            next_update=None,
            enabled=self.enabled,
            detail=detail,
            status=status,
        )
