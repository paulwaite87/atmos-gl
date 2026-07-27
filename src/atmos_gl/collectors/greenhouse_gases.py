#!/usr/bin/env python3
"""NASA GEOS-CF v2 CO2/CH4 -> file cache (Absolute mode's current-conditions source),
and Copernicus CAMS EGG4 reanalysis -> file cache (Anomaly mode's historical-baseline
source), for the greenhouse_gases layer.

Both collectors share the greenhouse_gases config section (species/mode/baseline_year/
per-species scale settings all live there for the frontend), but are scheduled and
reported to the Data Status page independently -- via settings_section (shared config)
kept distinct from section (own scheduling/status identity), the plain-CollectorBase
counterpart to FieldCollectorBase's status_name/section split -- because they hit
unrelated APIs with genuinely different fetch shapes:

  GeosCfGhgCollector      -- plain periodic HTTP download (OPeNDAP constraint URL, no
                             auth), file-cache pattern like SstCollector.
  CamsEgg4BaselineCollector -- rare, on-demand submit-then-poll job against the CDS
                             API (only fetches once per configured baseline_year, then
                             is done -- EGG4's historical data for a past year never
                             changes once published).

Unlike SST, no upstream source ships a pre-computed CO2/CH4 anomaly -- the
greenhouse_gases updater (tasks/greenhouse_gases.py) computes it itself from these two
collectors' cached fields via lib/greenhouse_gases.compute_anomaly().
"""
import concurrent.futures
import logging
import os

import cdsapi

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.data_status import build_status, read_process_status
from atmos_gl.lib.gfs import download_whole
from atmos_gl.lib.greenhouse_gases import (
    egg4_baseline_cache_path,
    geoscf_cache_path,
    resolve_baseline_year,
)

logger = logging.getLogger(__name__)

# GEOS-CF v2's "latest" forecast alias always resolves server-side to the newest
# available run, so this collector never has to probe/resolve a baseline itself
# (unlike GFS/RTOFS) -- chosen partly for this simplicity, see the GHG design grill.
#
# NOTE: collection name, variable names, and grid dimensions below are GEOS-CF v2's
# best-documented CO2/CH4-bearing collection as of this layer's design research --
# NASA's OPeNDAP metadata endpoints didn't return live confirmation during that
# research (see the published spec's "Further Notes"). Confirm against the live
# `<base_url>/{_GEOSCF_COLLECTION}.latest.info` endpoint and correct here if the
# collection/variable names differ.
_GEOSCF_COLLECTION = "xgc_tavg_1hr_g1440x721_x1"
_GEOSCF_CO2_VAR = "CO2"
_GEOSCF_CH4_VAR = "CH4"
_GEOSCF_NLAT = 721
_GEOSCF_NLON = 1440


def build_geoscf_url(base_url: str) -> str:
    """OPeNDAP constraint-expression URL for GEOS-CF's latest CO2+CH4 snapshot -- a
    single time step (index 0, the newest available), full global grid. Downloadable
    as a plain netCDF file over HTTP, same as any other download_whole() target."""
    lat_idx = f"0:1:{_GEOSCF_NLAT - 1}"
    lon_idx = f"0:1:{_GEOSCF_NLON - 1}"
    return (
        f"{base_url.rstrip('/')}/{_GEOSCF_COLLECTION}.latest.nc4?"
        f"{_GEOSCF_CO2_VAR}[0:1:0][{lat_idx}][{lon_idx}],"
        f"{_GEOSCF_CH4_VAR}[0:1:0][{lat_idx}][{lon_idx}],"
        f"lat[{lat_idx}],lon[{lon_idx}]"
    )


class GeosCfGhgCollector(CollectorBase):
    """Absolute mode's current-conditions source: NASA GEOS-CF v2, OPeNDAP, no API
    key. File-cache pattern (netCDF under {workdir}/data, not fieldstore) -- same
    architectural shape as SstCollector, since this is a single current snapshot, not
    a per-forecast-hour product."""

    section = "ghg_geoscf"
    settings_section = "greenhouse_gases"
    channel_key = "ghg_geoscf"
    datasource_key = "geoscf"
    display_label = "GEOS-CF (Greenhouse Gases)"

    def collect(self) -> None:
        base_url = self.datasource_url("geoscf")
        if not base_url:
            logger.warning("GEOS-CF GHG: no 'geoscf' datasource configured; skipping.")
            return

        url = build_geoscf_url(base_url)
        dest = geoscf_cache_path(self.workdir)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        logger.info(f"GEOS-CF GHG: downloading {url}")
        data = download_whole(url, timeout=300)

        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        logger.info(
            f"GEOS-CF GHG: wrote {len(data) / 1e6:.1f} MB -> {os.path.basename(dest)}"
        )


# Anomaly mode's baseline source: CAMS EGG4 reanalysis, 2003-2020 only.
#
# NOTE: dataset/variable names below are EGG4's best-documented CO2/CH4 identifiers as
# of this layer's design research -- confirm against the live ADS request-builder form
# for this dataset (https://ads.atmosphere.copernicus.eu/datasets/
# cams-global-ghg-reanalysis-egg4) and correct here if they differ (see the published
# spec's "Further Notes").
_EGG4_DATASET = "cams-global-ghg-reanalysis-egg4"

# Bounded-blocking cap agreed in the GHG design grill: a slow/queued CDS API job must
# not stall collect_once()'s single-threaded synchronous loop (which every other
# collector -- GFS, quakes, storms, ...  -- also runs through) indefinitely. On
# timeout, collect() logs and returns without raising, retried next cycle -- this is
# acceptable because the fetch is rare (only when baseline_year is unset or changed;
# a past reanalysis year's data never changes once cached).
_EGG4_TIMEOUT_S = 300


def build_egg4_request(year: int) -> dict:
    """CDS API request for CAMS EGG4's CO2 and CH4 fields for a full year at
    3-hourly cadence, NetCDF format."""
    return {
        "variable": ["co2_mass_mixing_ratio", "ch4_column_mean_molar_fraction"],
        "pressure_level": ["1000"],
        "year": str(year),
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(0, 24, 3)],
        "format": "netcdf",
    }


def _retrieve_with_timeout(client, dataset: str, request: dict, target: str, timeout_s: float):
    """Run client.retrieve() (cdsapi's own blocking submit-then-poll-then-download) in
    a worker thread, bounded by timeout_s. Raises concurrent.futures.TimeoutError if
    the job doesn't finish in time -- the calling thread stops waiting, but the
    worker thread (and the in-flight CDS job) is not cancelled; a future cycle's
    collect() will find the cache still missing and request again."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.retrieve, dataset, request, target)
        future.result(timeout=timeout_s)


class CamsEgg4BaselineCollector(CollectorBase):
    """Anomaly mode's historical-baseline source: Copernicus CAMS EGG4 reanalysis via
    the CDS API (`CDSAPI_KEY`). Fetches once per configured baseline_year and caches
    it -- a past reanalysis year's data never changes once published, so there is
    nothing to re-fetch once cached. File-cache pattern like GeosCfGhgCollector, but
    driven by a submit-then-poll job API rather than a plain HTTP GET, hence a
    separate collector class rather than a shared fetch path (see the GHG design
    grill for why the two fetch shapes weren't forced into one collector)."""

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

        api_key = os.environ.get("CDSAPI_KEY", "").strip()
        if not api_key:
            logger.warning("CAMS EGG4 baseline: no CDSAPI_KEY configured; skipping.")
            return

        base_url = self.datasource_url("cams_ads")
        if not base_url:
            logger.warning(
                "CAMS EGG4 baseline: no 'cams_ads' datasource configured; skipping."
            )
            return

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.tmp"
        client = cdsapi.Client(url=base_url, key=api_key)
        request = build_egg4_request(year)

        try:
            _retrieve_with_timeout(client, _EGG4_DATASET, request, tmp, _EGG4_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            logger.warning(
                f"CAMS EGG4 baseline: request for {year} timed out after "
                f"{_EGG4_TIMEOUT_S}s; will retry next cycle."
            )
            return
        except Exception as e:
            logger.error(f"CAMS EGG4 baseline: request for {year} failed: {e}")
            return

        os.replace(tmp, dest)
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
