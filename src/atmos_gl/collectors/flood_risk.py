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
import time
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
    jrc_tile_cache_path,
    load_gumbel_fit,
    load_jrc_tile_index,
    pad_glofas_grid_to_global_lat,
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

    Fetches, classifies, and stores each of GLOFAS_LEADTIME_HOURS as its OWN request
    (closer to SingleFileFieldCollector's per-hour shape than the original design,
    which requested all 7 leadtime days as one ~2.8GB ensemble netCDF per cycle since
    GloFAS's leadtime_hour field accepts a list). Confirmed live: the shared
    ECMWF/Copernicus object-store backend serving GloFAS can drop the connection
    repeatedly for stretches lasting well over an hour, which made the single combined
    job an all-or-nothing bet against its own 3-hour timeout -- a bad patch late in
    the transfer lost ALL 7 days' progress, not just the leadtime in flight. Per-hour
    requests mean a drop only costs the hour being fetched, each successfully-stored
    hour is immediately safe (in the field catalog, not just on disk), and a later
    self-gated cycle resumes on whichever hours are still missing rather than
    restarting the whole run from zero -- see _resume_run_date_str.
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

    # In-memory last-attempt marker (monotonic clock), keyed on the class rather than an
    # instance since FieldCollectorDriver constructs a fresh instance every cycle
    # (collectors/service.py's _collect_fields() docstring) but this class object persists
    # for the life of the data_collector process. Deliberately NOT read from process_status:
    # FieldCollectorDriver._drive_one() (driving.py) calls record_process_start() -- which
    # overwrites that row's timestamp -- BEFORE collect() ever runs, so by the time this
    # method executes, process_status already reflects the CURRENT attempt, not the
    # previous one. FieldCollectorDriver, unlike EventFeedDriver, has no is_stale() cadence
    # check of its own (see driving.py's docstring: it's built for GFS/RTOFS's incremental
    # per-hour dedup, not a single whole-run-per-day fetch like this collector's), so
    # flood_risk.runs_per_day was configured but silently had no effect on Live mode --
    # confirmed live on prod: repeated EWDS requests fired every ~15-30min service cycle
    # regardless of whether the previous one had even finished, racing/aborting each other.
    # This self-gate (mirrors CollectorBase.is_stale()'s own monotonic-clock convention)
    # makes that setting actually take effect.
    _last_attempt_monotonic: float | None = None

    def collect(self, ctx: CycleContext) -> None:
        now_mono = time.monotonic()
        last = FloodRiskLiveCollector._last_attempt_monotonic
        if last is not None and (now_mono - last) < self.period_s:
            logger.debug(
                f"{self.status_name}: not yet due "
                f"(period {self.period_s:.0f}s, {now_mono - last:.0f}s since last attempt); "
                f"skipping."
            )
            return
        FloodRiskLiveCollector._last_attempt_monotonic = now_mono

        creds = resolve_ewds_credentials(self.datasource_url, self.status_name)
        if creds is None:
            return
        base_url, api_key = creds

        now = datetime.now(timezone.utc)
        run_date_str = self._resume_run_date_str(now)
        hours_to_fetch = [
            h for h in GLOFAS_LEADTIME_HOURS
            if run_date_str is None
            or not self.store.field_exists(run_date_str, _RUN_ID, int(h), _PRODUCT)
        ]
        if run_date_str is not None and not hours_to_fetch:
            logger.debug(f"{self.status_name}: {run_date_str}: already fully stored; skipping.")
            return

        client = cdsapi.Client(url=base_url, key=api_key)
        gumbel_fit = None  # (loc_native, scale_native, gumbel_lat, gumbel_lon), loaded lazily
        stored = 0

        for leadtime_hour in hours_to_fetch:
            if run_date_str is None:
                # Run not locked in yet -- search the same freshest-first candidate
                # dates as before, but for this one leadtime hour only.
                requests = [
                    build_glofas_forecast_request(
                        (now - timedelta(days=d)).strftime("%Y%m%d"), leadtime_hour
                    )
                    for d in range(GLOFAS_SEARCH_DAYS)
                ]
            else:
                requests = [build_glofas_forecast_request(run_date_str, leadtime_hour)]

            dest = glofas_forecast_cache_path(self.workdir, leadtime_hour)
            ok = retrieve_with_fallback(
                client, GLOFAS_DATASET, requests, dest, GLOFAS_TIMEOUT_S,
                self.status_name, unzip=False,
            )
            if not ok:
                # Stop this cycle here -- a queued/still-failing candidate shouldn't be
                # hammered with more requests. The next self-gated cycle resumes on
                # this same hour (and whatever follows it) via _resume_run_date_str.
                break

            if gumbel_fit is None:
                try:
                    gumbel_path = ensure_gumbel_fit_cached()
                except Exception as e:
                    logger.warning(
                        f"{self.status_name}: Gumbel-fit threshold data unavailable ({e}); "
                        f"skipping."
                    )
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    break
                gumbel_fit = load_gumbel_fit(gumbel_path)

            try:
                fetched_run_date_str, fhour = self._process_and_store_one_hour(dest, *gumbel_fit)
                run_date_str = fetched_run_date_str
                stored += 1
            finally:
                try:
                    os.remove(dest)
                except OSError:
                    pass

        if stored:
            self.store.prune_except_run(run_date_str, _RUN_ID, products=[_PRODUCT])
            logger.info(f"{self.status_name}: {run_date_str}: stored {stored} leadtime hour(s) this cycle.")

    def _resume_run_date_str(self, now: datetime) -> str | None:
        """The date of a previously-locked-in run still worth continuing -- the field
        catalog already has SOME (but not necessarily all) of GLOFAS_LEADTIME_HOURS
        stored for it, and it's recent enough (within GLOFAS_SEARCH_DAYS) that
        resuming beats re-searching for a fresher one. Lets collect() pick up exactly
        where an interrupted cycle (connection drop, OOM, container restart) left off
        without re-fetching hours already safely stored. None on a cold start, or if
        the latest stored run is stale enough that a fresher one should be sought
        instead."""
        latest = self.store.field_catalog_adapter.get_latest_run_hours(products=[_PRODUCT])
        if not latest or not latest.get("hours"):
            return None
        run_date = latest["run_date"]
        run_date_str = run_date.strftime("%Y%m%d") if hasattr(run_date, "strftime") else str(run_date)
        run_date_dt = datetime.strptime(run_date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        if (now - run_date_dt).days > GLOFAS_SEARCH_DAYS:
            return None
        return run_date_str

    def _process_and_store_one_hour(
        self, nc_path: str, loc_native, scale_native, gumbel_lat, gumbel_lon
    ) -> tuple[str, int]:
        """Classify and store ONE already-downloaded single-leadtime-hour GloFAS
        netCDF against ETH's Gumbel-fit thresholds, regridded onto this file's own
        lat/lon (invariant across leadtime hours of the same run, but cheap enough --
        a nearest-neighbor lookup, not a network fetch -- to just redo per hour rather
        than threading a cached regrid result through collect()'s resumable loop).
        Returns (run_date_str, fhour) as reported by the file's own metadata, for
        collect()'s bookkeeping."""
        with xr.open_dataset(nc_path) as ds:
            ds = ds.isel(forecast_reference_time=0)
            run_timestamp = pd.Timestamp(ds["forecast_reference_time"].values).to_pydatetime()
            run_timestamp = run_timestamp.replace(tzinfo=timezone.utc)
            run_date_str = run_timestamp.strftime("%Y%m%d")

            lat = ds["latitude"].values
            lon = ds["longitude"].values
            loc = regrid_nearest(loc_native, gumbel_lat, gumbel_lon, lat, lon)
            scale = regrid_nearest(scale_native, gumbel_lat, gumbel_lon, lat, lon)

            if "forecast_period" in ds.dims:
                ds = ds.isel(forecast_period=0)
            fhour = int(ds["forecast_period"].values / np.timedelta64(1, "h"))
            ensemble_discharge = ds["dis24"].values  # (number, lat, lon)

            band, fraction = ensemble_severity_band(ensemble_discharge, loc, scale)
            valid_time = run_timestamp + timedelta(hours=fhour)

            # GloFAS's own domain stops at ~-60 lat (see pad_glofas_grid_to_global_lat's
            # docstring) -- pad to a full -90..90 grid before storing so this product's
            # texture spans the same latitude range every other layer's does.
            band_full, lat_full = pad_glofas_grid_to_global_lat(band.astype(np.float32), lat)
            fraction_full, _ = pad_glofas_grid_to_global_lat(fraction.astype(np.float32), lat)

            self.store.store_field(
                run_date_str, _RUN_ID, fhour, _PRODUCT,
                {
                    "lat": lat_full,
                    "lon": lon,
                    "values": band_full,
                    "values2": fraction_full,
                },
                valid_time,
            )

        return run_date_str, fhour

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
            # next_update ignores self.enabled deliberately, matching
            # freshness_data_status()'s next_update_respects_enabled=False default
            # (lib/data_status.py): self.enabled here resolves to flood_risk.enabled
            # (settings_section is overridden to the shared "flood_risk" section,
            # same as CACHE_COLLECTORS' own convention) -- the layer's frontend
            # Show-toggle, NOT a collection kill-switch. _collect_fields() drives this
            # collector every cycle unconditionally of it (gated only by
            # channel_enabled["flood_risk_live"]), so reporting "next: disabled" here
            # would misleadingly suggest collection itself had stopped just because
            # the layer isn't currently shown on the map.
            next_update=estimate_next_update(last_updated, period_s, True),
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

    # collect() runs synchronously inside CollectorService.collect_once()'s single
    # sequential sweep (collectors/service.py) -- everything after this collector in
    # that sweep (event feeds, then GFS/RTOFS field ingestion), AND the
    # "data_collector" service heartbeat itself, all wait for collect() to return.
    # Downloading every remaining tile in one call can take long enough (network
    # latency x up to 271 tiles, ~515MB total -- ensure_jrc_tile_cached()'s own
    # docstring notes this host observed mid-transfer failures on a 271-tile batch)
    # to push that heartbeat past the Data Status page's dead threshold, which reads
    # as the WHOLE data_collector service being down even though it's just busy with
    # this one-time historical backfill. Capping actual NEW downloads per call
    # (already-cached tiles are free -- they don't count) bounds collect()'s
    # wall-clock time regardless of how many tiles remain, spreading the initial
    # backfill across is_stale()'s normal hourly cadence instead -- matching this
    # class's own docstring, which already claimed (but didn't enforce) that shape.
    _MAX_NEW_TILE_DOWNLOADS_PER_CYCLE = 30

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
        new_downloads = 0
        for tile in tiles:
            already_cached = os.path.exists(jrc_tile_cache_path(tile["id"], tile["name"]))
            if not already_cached and new_downloads >= self._MAX_NEW_TILE_DOWNLOADS_PER_CYCLE:
                logger.info(
                    f"{self.section}: per-cycle download budget "
                    f"({self._MAX_NEW_TILE_DOWNLOADS_PER_CYCLE} new tiles) reached "
                    f"({cached_count}/{len(tiles)} cached so far); will resume next cycle."
                )
                return

            try:
                tile_path = ensure_jrc_tile_cached(tile["id"], tile["name"])
            except Exception as e:
                logger.warning(
                    f"{self.section}: tile {tile['name']!r} unavailable this cycle "
                    f"({e}); will retry next cycle."
                )
                continue
            if not already_cached:
                new_downloads += 1
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
