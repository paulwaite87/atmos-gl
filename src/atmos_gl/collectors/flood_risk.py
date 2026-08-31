#!/usr/bin/env python3
"""Flood Risk layer collectors -- see issue #371.

  FloodRiskLiveCollector       -- Live mode: Copernicus GloFAS ensemble discharge
                                  forecast, classified per grid cell against ETH's
                                  published Gumbel-fit return-period thresholds.
  FloodRiskHistoricalCollector -- Historical mode: JRC Global River Flood Hazard
                                  Maps (100-year return period), mosaicked once
                                  into a single global raster and cached forever.

Both share one settings_section ("flood_risk", holding the shared mode toggle) but
keep independent `section`/channel identities, same split as the greenhouse_gases
layer's forecast/baseline pair.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import cdsapi
import numpy as np
import pandas as pd
import xarray as xr

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.collectors.field_base import CycleContext, FieldCollectorBase
from atmos_gl.lib.cds_client import resolve_ewds_credentials, retrieve_with_fallback
from atmos_gl.lib.data_status import build_status, estimate_next_update, read_process_status
from atmos_gl.lib.flood_risk import (
    GLOFAS_DATASET,
    GLOFAS_LEADTIME_HOURS,
    GLOFAS_SEARCH_DAYS,
    GLOFAS_TIMEOUT_S,
    JRC_BASE_URL,
    build_glofas_forecast_request,
    build_jrc_mosaic_grid,
    count_cached_jrc_tiles,
    ensemble_severity_band,
    ensure_gumbel_fit_cached,
    ensure_jrc_tile_cached,
    ensure_jrc_tile_extents_cached,
    glofas_forecast_cache_path,
    jrc_hazard_mosaic_cache_path,
    load_gumbel_fit,
    load_jrc_tile_index,
    regrid_nearest,
    resample_jrc_tile_onto_grid,
    save_jrc_hazard_mosaic,
    tile_dst_window,
)

logger = logging.getLogger(__name__)

_PRODUCT = "flood_risk_live"
# GloFAS issues exactly one forecast run per day -- no 00Z/12Z multi-cycle concept
# like GFS -- so run_id is a fixed placeholder, not a real cycle identifier.
_RUN_ID = "00"


class FloodRiskLiveCollector(FieldCollectorBase):
    """Live mode: today's GloFAS ensemble discharge forecast, classified per grid
    cell into GloFAS's own 2yr/5yr/20yr return-period severity bands against ETH's
    published Gumbel-fit thresholds -- see issue #371's Implementation Decisions.

    Deliberately does NOT use SingleFileFieldCollector's one-file-per-hour shape:
    GloFAS's leadtime_hour field accepts a list, so all 7 of GLOFAS_LEADTIME_HOURS
    are fetched in ONE ensemble netCDF per cycle (confirmed live during issue #371's
    spike), then split into per-leadtime fields here -- the inverse of
    SingleFileFieldCollector's per-hour download loop.
    """

    section = "flood_risk"
    settings_section = "flood_risk"
    status_name = "flood_risk_live"
    channel_key = "flood_risk_live"
    datasource_key = "glofas_ews"
    baseline_key = "glofas"
    products = {_PRODUCT: None}
    display_label = "GloFAS Flood Risk (Live)"

    def base_url(self) -> str | None:
        """Overridden: FieldCollectorBase's default reads self.settings["datasources"],
        which assumes settings_section == "data_collector" (true for GFS/RTOFS, NOT for
        this collector -- settings_section is "flood_risk", its own section, matching
        the greenhouse_gases/air_quality CACHE_COLLECTORS convention instead). Without
        this override, drain_backfill()'s `if not collector.base_url()` check would
        always see an empty dict and misreport "no datasource configured" even when
        glofas_ews IS set. Delegates to CollectorBase.datasource_url(), the same
        data_collector.datasources-resolving mechanism collect() itself already uses
        via resolve_ewds_credentials."""
        bu = self.datasource_url(self.datasource_key)
        return bu.rstrip("/") if bu else None

    def resolve_baseline(self, base_url):
        """Not used. GloFAS has no lightweight per-hour sidecar to probe the way GFS's
        .idx files allow (see resolve_gfs_baseline) -- "does a run exist" can only be
        answered by the same retrieve_with_fallback call collect() already makes, so
        baseline resolution happens inline there instead. Left raising
        NotImplementedError (the FieldCollectorBase default)."""
        raise NotImplementedError(
            "FloodRiskLiveCollector resolves its baseline inline in collect()"
        )

    def collect(self, ctx: CycleContext) -> None:
        creds = resolve_ewds_credentials(self.datasource_url, self.status_name)
        if creds is None:
            return
        base_url, api_key = creds

        dest = glofas_forecast_cache_path(self.workdir)
        if self._latest_run_already_stored(dest):
            logger.debug(
                f"{self.status_name}: latest cached run already fully stored; skipping re-fetch."
            )
            return

        client = cdsapi.Client(url=base_url, key=api_key)
        now = datetime.now(timezone.utc)
        candidate_requests = [
            build_glofas_forecast_request((now - timedelta(days=d)).strftime("%Y%m%d"))
            for d in range(GLOFAS_SEARCH_DAYS)
        ]
        ok = retrieve_with_fallback(
            client, GLOFAS_DATASET, candidate_requests, dest, GLOFAS_TIMEOUT_S,
            self.status_name, unzip=False,
        )
        if not ok:
            return

        try:
            gumbel_path = ensure_gumbel_fit_cached()
        except Exception as e:
            logger.warning(
                f"{self.status_name}: Gumbel-fit threshold data unavailable ({e}); skipping."
            )
            return

        self._process_and_store(dest, gumbel_path)

    def _latest_run_already_stored(self, cached_path: str) -> bool:
        """True if the run in the currently-cached netCDF (if any) already has every
        GLOFAS_LEADTIME_HOURS entry in the field catalog -- skips a redundant re-fetch
        of a run that hasn't changed since the last successful cycle (GloFAS publishes
        once per day). Opens only the cheap scalar coordinate, not the full ensemble
        arrays."""
        if not os.path.exists(cached_path):
            return False
        try:
            with xr.open_dataset(cached_path) as ds:
                # forecast_reference_time is always a length-1 dimension (confirmed
                # live during issue #371's spike) -- isel(...=0) first, matching
                # _process_and_store's own extraction, since pd.Timestamp() rejects
                # an array-like value rather than a scalar.
                ref_time = ds["forecast_reference_time"].isel(forecast_reference_time=0)
                run_timestamp = pd.Timestamp(ref_time.values).to_pydatetime()
            run_date_str = run_timestamp.strftime("%Y%m%d")
            last_hour = int(GLOFAS_LEADTIME_HOURS[-1])
            return self.store.field_exists(run_date_str, _RUN_ID, last_hour, _PRODUCT)
        except Exception:
            return False

    def _process_and_store(self, forecast_path: str, gumbel_path: str) -> None:
        loc_native, scale_native, gumbel_lat, gumbel_lon = load_gumbel_fit(gumbel_path)

        with xr.open_dataset(forecast_path) as ds:
            ds = ds.isel(forecast_reference_time=0)
            run_timestamp = pd.Timestamp(ds["forecast_reference_time"].values).to_pydatetime()
            run_timestamp = run_timestamp.replace(tzinfo=timezone.utc)
            run_date_str = run_timestamp.strftime("%Y%m%d")

            lat = ds["latitude"].values
            lon = ds["longitude"].values

            # Regridded ONCE per cycle (the Gumbel grid is invariant across leadtimes),
            # not per leadtime-day below.
            loc = regrid_nearest(loc_native, gumbel_lat, gumbel_lon, lat, lon)
            scale = regrid_nearest(scale_native, gumbel_lat, gumbel_lon, lat, lon)

            stored = 0
            for i in range(ds.sizes["forecast_period"]):
                period = ds["forecast_period"].values[i]
                fhour = int(period / np.timedelta64(1, "h"))
                ensemble_discharge = ds["dis24"].isel(forecast_period=i).values  # (number, lat, lon)

                band, fraction = ensemble_severity_band(ensemble_discharge, loc, scale)
                valid_time = run_timestamp + timedelta(hours=fhour)

                self.store.store_field(
                    run_date_str, _RUN_ID, fhour, _PRODUCT,
                    {
                        "lat": lat,
                        "lon": lon,
                        "values": band.astype(np.float32),
                        "values2": fraction.astype(np.float32),
                    },
                    valid_time,
                )
                stored += 1

        logger.info(f"{self.status_name}: {run_date_str}: stored {stored} leadtime day(s).")

    def data_status(self) -> dict:
        """Overridden because FieldCollectorBase's default assumes a continuous hourly
        forecast window -- GLOFAS_LEADTIME_HOURS is a sparse 7-value set (24h steps
        over 7 days), which the base formula would score against ~168 expected hourly
        slots and wildly underreport. Percent here is simply "how many of the 7
        expected leadtime days does the latest run have."."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.status_name
        )
        expected = {int(h) for h in GLOFAS_LEADTIME_HOURS}
        avail = self.store.field_catalog_adapter.get_latest_run_hours(products=[_PRODUCT])
        percent = 0.0
        detail = last_error
        if avail and avail.get("hours"):
            present = expected & set(avail["hours"])
            percent = 100.0 * len(present) / len(expected)
            if not detail:
                run_date = avail["run_date"]
                run_date_str = (
                    run_date.isoformat() if hasattr(run_date, "isoformat") else str(run_date)
                )
                detail = f"{run_date_str}: {len(present)}/{len(expected)} leadtime day(s)"

        period_s = self._service_period_s()
        return build_status(
            name=self.status_name,
            kind="collector",
            percent=percent,
            last_updated=last_updated,
            next_update=estimate_next_update(last_updated, period_s, self.enabled),
            enabled=self.enabled,
            detail=detail,
            status=status,
        )


class FloodRiskHistoricalCollector(CollectorBase):
    """Historical mode: JRC Global River Flood Hazard Maps at the 100-year return
    period, mosaicked once into a single global raster and cached forever -- a
    fixed, terrain/floodplain-derived hazard classification, unlike Live mode's
    daily-refreshed GloFAS forecast. CollectorBase (fetch-once style, like
    CamsEgg4BaselineCollector/GSHHG) is correct here, not FieldCollectorBase: there
    is no time dimension at all to store per-forecast-hour.

    271 tiles (~515MB total, RP100 reclass variant only, see lib/flood_risk.py's
    module docstring) are downloaded across however many collect() cycles it
    takes -- ensure_jrc_tile_cached() skips tiles already on disk, so a partial
    pass just resumes next cycle rather than re-downloading from scratch. The
    final mosaic is only built and cached once ALL 271 tiles have downloaded
    successfully in one pass; a partial pass logs progress and returns, leaving
    the (nonexistent) mosaic cache file as the "not yet done" signal for the next
    cycle -- no separate "download complete" flag needed.
    """

    section = "flood_risk_historical"
    settings_section = "flood_risk"
    channel_key = "flood_risk_historical"
    display_label = "JRC Flood Hazard (Historical)"

    def source_url(self) -> str | None:
        """Overridden: hardcoded open-FTP source, not a data_collector.datasources
        entry -- same "no config datasource" convention as StormsCollector's own
        ATCF mirror URLs (see CollectorBase.datasource_key's docstring)."""
        return JRC_BASE_URL

    def collect(self) -> None:
        dest = jrc_hazard_mosaic_cache_path(self.workdir)
        if os.path.exists(dest):
            logger.debug(f"{self.section}: mosaic already cached; skipping.")
            return

        try:
            index_path = ensure_jrc_tile_extents_cached()
            tiles = load_jrc_tile_index(index_path)
        except Exception as e:
            logger.warning(f"{self.section}: tile index unavailable ({e}); skipping this cycle.")
            return

        lat, lon = build_jrc_mosaic_grid()
        mosaic = np.zeros((len(lat), len(lon)), dtype=np.uint8)

        cached_count = 0
        for tile in tiles:
            try:
                tile_path = ensure_jrc_tile_cached(tile["id"], tile["name"])
            except Exception as e:
                logger.warning(
                    f"{self.section}: tile {tile['name']!r} unavailable this cycle "
                    f"({e}); will retry next cycle."
                )
                continue
            cached_count += 1

            row0, row1, col0, col1 = tile_dst_window(tile["bounds"])
            mosaic[row0:row1, col0:col1] = resample_jrc_tile_onto_grid(
                tile_path, lat[row0:row1], lon[col0:col1]
            )

        if cached_count < len(tiles):
            logger.info(
                f"{self.section}: {cached_count}/{len(tiles)} tiles cached so far; "
                f"mosaic not yet complete."
            )
            return

        save_jrc_hazard_mosaic(dest, mosaic, lat, lon)
        logger.info(f"{self.section}: mosaic complete ({len(tiles)} tiles) -> {dest}")

    def data_status(self) -> dict:
        """Coverage-based, not time-decay -- same reasoning as
        CamsEgg4BaselineCollector.data_status(): this collector fetches once (across
        however many cycles the 271-tile download takes) then is permanently done,
        so a decaying-freshness formula would show perpetual staleness for a source
        working exactly as designed. Percent tracks tile-download progress until the
        mosaic itself is cached, at which point it's simply 100."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        mosaic_cached = os.path.exists(jrc_hazard_mosaic_cache_path(self.workdir))
        counts = count_cached_jrc_tiles()

        if mosaic_cached:
            percent = 100.0
            detail = last_error or "mosaic cached"
        elif counts:
            cached, total = counts
            percent = 100.0 * cached / total if total else 0.0
            detail = last_error or f"{cached}/{total} tiles cached"
        else:
            percent = 0.0
            detail = last_error or "tile index not yet fetched"

        return build_status(
            name=self.section,
            kind="collector",
            percent=percent,
            last_updated=last_updated,
            next_update=None,
            enabled=self.enabled,
            detail=detail,
            status=status,
        )
