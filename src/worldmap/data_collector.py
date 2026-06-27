#!/usr/bin/env python3
import os
import glob
import logging
import asyncio
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel
from worldmap.lib import fieldstore
from worldmap.lib.gfs import (
    ATMOS_TARGETS,
    resolve_gfs_baseline_with_coverage,
    gfs_index_ranges,
    download_byte_ranges,
    download_whole,
    remote_exists,
    build_atmos_url,
    build_wave_url,
)
from worldmap.lib.rtofs import (
    resolve_rtofs_baseline,
    build_currents_url,
    build_currents_nowcast_url,
    RTOFS_MAX_HOURLY_FHOUR,
)
from worldmap.lib.unpack import ATMOS_UNPACKERS, CURRENTS_UNPACKERS, WAVES_UNPACKERS
from worldmap.lib.oisst import build_oisst_url, oisst_cache_path, remote_is_newer
from worldmap.lib.gibs import build_clouds_url, clouds_cache_path

logger = logging.getLogger("worldmap.data_collector")


class DataCollector:
    """Background process that pre-fetches and UNPACKS data into the database.

    For each configured datasource it downloads whole forecast hours from 'now' forward
    (cache_hours of them), decodes each layer/product into plain numeric arrays
    (lat, lon, values...) via worldmap.lib.unpack, and stores those keyed by
    (date, run, fhour, product). Tasks then read pre-processed fields from the DB and
    only have to clip + render, so plot() is fast. Nothing is stored as a raw GRIB blob.

    Datasource handlers:
      * gfs      - atmospheric pgrb2.0p25 union -> isobars/precip/temperature/ozone/
                   wind/stormwatch (implemented here). wave product: TODO.
      * currents - RTOFS (TODO)
      * sst      - OISST (TODO)
    """

    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        # Bind the fieldstore to this process's workdir + db handle. Bulk field
        # arrays live as compressed files under {workdir}/fields; the db keeps
        # only the catalog rows.
        workdir = self.config.get_setting("common", "workdir", ".")
        self.workdir = workdir
        self.store = fieldstore.get_store(workdir, db=self.db)
        logger.debug("Initializing Data Collector")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("data_collector")
        self.datasources = self.settings.get("datasources", {})
        # Full-refresh cadence. Prefer update_minutes (finer control); fall back to the
        # legacy update_hours for existing configs. Stored as seconds.
        if self.settings.get("update_minutes") is not None:
            self.update_period_s = int(self.settings.get("update_minutes")) * 60
        else:
            self.update_period_s = int(self.settings.get("update_hours", 12)) * 3600
        self.cache_hours = int(self.settings.get("cache_hours", 24))
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)

    # -- GFS atmospheric union ------------------------------------------------
    def _collect_gfs_atmos(self, base_url):
        baseline = resolve_gfs_baseline_with_coverage(base_url, self.cache_hours)
        if not baseline:
            logger.warning(
                "Data Collector: could not resolve a GFS baseline; will retry."
            )
            return

        run_date_str, run_id, run_timestamp = (
            baseline["date_str"],
            baseline["run"],
            baseline["timestamp"],
        )
        now = datetime.now(timezone.utc)
        hours_since_run = int(round((now - run_timestamp).total_seconds() / 3600.0))
        fhour_0 = max(0, hours_since_run)  # forecast hour valid 'now' (no user offset)
        fhour_end = fhour_0 + self.cache_hours

        products = list(ATMOS_UNPACKERS.items())
        stored = 0

        for fhour in range(fhour_0, fhour_end):
            valid = run_timestamp + timedelta(hours=fhour)

            # Which products still need this hour? Skip the download entirely if none.
            missing = [
                (product, unpacker)
                for (product, unpacker) in products
                if not self.store.field_exists(run_date_str, run_id, fhour, product)
            ]
            if not missing:
                continue

            aurl = build_atmos_url(base_url, run_date_str, run_id, fhour)
            try:
                ranges = gfs_index_ranges(aurl, ATMOS_TARGETS)
                if not ranges:
                    logger.debug(f"atmos f{fhour:03d}: index not ready yet")
                    continue
                data = download_byte_ranges(aurl, ranges)
                if not data:
                    continue
            except Exception as e:
                logger.debug(f"atmos f{fhour:03d} download skipped: {e}")
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
            tmp.write(data)
            tmp.close()
            try:
                for product, unpacker in missing:
                    try:
                        fields = unpacker(tmp.name)
                        self.store.store_field(
                            run_date_str, run_id, fhour, product, fields, valid
                        )
                        stored += 1
                    except Exception as e:
                        logger.debug(f"{product} f{fhour:03d} unpack/store failed: {e}")
            finally:
                # Remove the temp GRIB and any cfgrib .idx sidecars it created.
                for path in [tmp.name] + glob.glob(tmp.name + "*.idx"):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        logger.info(
            f"Data Collector (gfs): {run_date_str} {run_id}Z, hours {fhour_0:03d}..{fhour_end - 1:03d}; "
            f"stored {stored} field(s)."
        )
        try:
            self.store.prune_except_run(
                run_date_str, run_id, products=list(ATMOS_UNPACKERS.keys())
            )
        except Exception as e:
            logger.debug(f"prune skipped: {e}")

    # -- GFS-Wave swell -------------------------------------------------------
    def _collect_gfs_waves(self, base_url):
        """Ingest the per-hour GFS-Wave global 0p25 swell field, on the SAME GFS run and
        forecast-hour cadence as the atmospheric products, so waves shares the GFS
        timeline. Each hour is a separate small (~0.6 MB) GRIB, downloaded whole (vs the
        atmos byte-range union), unpacked to a swell u/v field, and stored under the
        'waves' product. Mirrors _collect_gfs_atmos's window + skip-if-present logic."""
        baseline = resolve_gfs_baseline_with_coverage(base_url, self.cache_hours)
        if not baseline:
            logger.warning(
                "Data Collector: could not resolve a GFS baseline for waves; will retry."
            )
            return

        run_date_str, run_id, run_timestamp = (
            baseline["date_str"],
            baseline["run"],
            baseline["timestamp"],
        )
        now = datetime.now(timezone.utc)
        hours_since_run = int(round((now - run_timestamp).total_seconds() / 3600.0))
        fhour_0 = max(0, hours_since_run)  # forecast hour valid 'now'
        fhour_end = fhour_0 + self.cache_hours

        product, unpacker = next(iter(WAVES_UNPACKERS.items()))
        stored = 0

        for fhour in range(fhour_0, fhour_end):
            if self.store.field_exists(run_date_str, run_id, fhour, product):
                continue

            valid = run_timestamp + timedelta(hours=fhour)
            url = build_wave_url(base_url, run_date_str, run_id, fhour)
            if not remote_exists(url):
                logger.debug(f"waves f{fhour:03d}: not published yet")
                continue

            try:
                data = download_whole(url)
                if not data:
                    continue
            except Exception as e:
                logger.debug(f"waves f{fhour:03d} download skipped: {e}")
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
            tmp.write(data)
            tmp.close()
            try:
                fields = unpacker(tmp.name)
                self.store.store_field(
                    run_date_str, run_id, fhour, product, fields, valid
                )
                stored += 1
            except Exception as e:
                logger.debug(f"waves f{fhour:03d} unpack/store failed: {e}")
            finally:
                for path in [tmp.name] + glob.glob(tmp.name + "*.idx"):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        logger.info(
            f"Data Collector (waves): {run_date_str} {run_id}Z, "
            f"hours {fhour_0:03d}..{fhour_end - 1:03d}; stored {stored} field(s)."
        )
        try:
            self.store.prune_except_run(run_date_str, run_id, products=[product])
        except Exception as e:
            logger.debug(f"waves prune skipped: {e}")

    # -- RTOFS ocean currents -------------------------------------------------
    def _collect_rtofs_currents(self, base_url):
        baseline = resolve_rtofs_baseline(base_url)
        if not baseline:
            logger.warning(
                "Data Collector: could not resolve an RTOFS baseline; will retry."
            )
            return

        date_str, run, ts = (
            baseline["date_str"],
            baseline["run"],
            baseline["timestamp"],
        )
        now = datetime.now(timezone.utc)
        hours_since_run = int(round((now - ts).total_seconds() / 3600.0))
        fhour_0 = max(0, hours_since_run)  # forecast hour valid 'now'

        # RTOFS surface files are hourly only to f072; cap the cache window so the
        # simple hourly loop never requests a non-existent (3-hourly) hour.
        fhour_end = min(fhour_0 + self.cache_hours, RTOFS_MAX_HOURLY_FHOUR + 1)
        if fhour_0 > RTOFS_MAX_HOURLY_FHOUR:
            logger.warning(
                f"RTOFS run {date_str} is {fhour_0}h old (> {RTOFS_MAX_HOURLY_FHOUR}h "
                f"hourly limit); a newer run should appear shortly."
            )
            return

        product, unpacker = next(iter(CURRENTS_UNPACKERS.items()))
        stored = 0

        for fhour in range(fhour_0, fhour_end):
            if self.store.field_exists(date_str, run, fhour, product):
                continue

            valid = ts + timedelta(hours=fhour)
            url = build_currents_url(base_url, date_str, fhour)
            # Fall back to the nowcast (present conditions) if this forecast hour
            # isn't published yet; better a current 'now' field than a gap.
            if not remote_exists(url):
                fallback = build_currents_nowcast_url(base_url, date_str)
                if fhour == fhour_0 and remote_exists(fallback):
                    logger.debug(f"currents f{fhour:03d} missing; using n000 nowcast")
                    url = fallback
                else:
                    logger.debug(f"currents f{fhour:03d}: not published yet")
                    continue

            try:
                data = download_whole(url)
                if not data:
                    continue
            except Exception as e:
                logger.debug(f"currents f{fhour:03d} download skipped: {e}")
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
            tmp.write(data)
            tmp.close()
            try:
                fields = unpacker(tmp.name)
                self.store.store_field(date_str, run, fhour, product, fields, valid)
                stored += 1
            except Exception as e:
                logger.debug(f"currents f{fhour:03d} unpack/store failed: {e}")
            finally:
                try:
                    os.remove(tmp.name)
                except OSError:
                    pass

        logger.info(
            f"Data Collector (currents): {date_str} {run}Z, hours "
            f"{fhour_0:03d}..{fhour_end - 1:03d}; stored {stored} field(s)."
        )
        try:
            self.store.prune_except_run(
                date_str, run, products=list(CURRENTS_UNPACKERS.keys())
            )
        except Exception as e:
            logger.debug(f"currents prune skipped: {e}")

    # -- dispatch -------------------------------------------------------------
    def _gfs_base_url(self):
        """The base URL configured for the 'gfs' datasource (atmos + waves share it)."""
        bu = self.datasources.get("gfs")
        return bu.rstrip("/") if bu else None

    def _backfill_atmos_hour(
        self, base_url, run_date, run_id, fhour, product, unpacker
    ):
        """Fetch a single atmos product for one (date, run, hour) via the byte-range
        path, mirroring _collect_gfs_atmos's inner body for exactly one hour/product."""
        aurl = build_atmos_url(base_url, run_date, run_id, fhour)
        ranges = gfs_index_ranges(aurl, ATMOS_TARGETS)
        if not ranges:
            return False
        data = download_byte_ranges(aurl, ranges)
        if not data:
            return False
        valid = self._valid_time(run_date, run_id, fhour)
        tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
        tmp.write(data)
        tmp.close()
        try:
            fields = unpacker(tmp.name)
            self.store.store_field(run_date, run_id, fhour, product, fields, valid)
            return True
        finally:
            for path in [tmp.name] + glob.glob(tmp.name + "*.idx"):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _backfill_waves_hour(
        self, base_url, run_date, run_id, fhour, product, unpacker
    ):
        """Fetch the GFS-Wave global 0p25 GRIB for one hour (whole-file), mirroring
        _collect_gfs_waves's inner body for exactly one hour."""
        url = build_wave_url(base_url, run_date, run_id, fhour)
        if not remote_exists(url):
            return False
        data = download_whole(url)
        if not data:
            return False
        valid = self._valid_time(run_date, run_id, fhour)
        tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
        tmp.write(data)
        tmp.close()
        try:
            fields = unpacker(tmp.name)
            self.store.store_field(run_date, run_id, fhour, product, fields, valid)
            return True
        finally:
            for path in [tmp.name] + glob.glob(tmp.name + "*.idx"):
                try:
                    os.remove(path)
                except OSError:
                    pass

    @staticmethod
    def _valid_time(run_date, run_id, fhour):
        run_ts = datetime.strptime(f"{run_date} {run_id}", "%Y-%m-%d %H").replace(
            tzinfo=timezone.utc
        )
        return run_ts + timedelta(hours=int(fhour))

    def _currents_base_url(self):
        """The base URL configured for the 'currents' (RTOFS) datasource."""
        bu = self.datasources.get("currents")
        return bu.rstrip("/") if bu else None

    def _backfill_currents_hour(
        self, base_url, date_str, run, fhour, product, unpacker
    ):
        """Fetch a single RTOFS currents hour on demand. RTOFS URLs key off date + fhour
        (one daily cycle), with the nowcast as a fallback when the forecast hour isn't
        published. Mirrors _collect_rtofs_currents's inner body for one hour."""
        url = build_currents_url(base_url, date_str, fhour)
        if not remote_exists(url):
            fallback = build_currents_nowcast_url(base_url, date_str)
            if remote_exists(fallback):
                url = fallback
            else:
                return False
        data = download_whole(url)
        if not data:
            return False
        valid = self._valid_time(date_str, run, fhour)
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
        tmp.write(data)
        tmp.close()
        try:
            fields = unpacker(tmp.name)
            self.store.store_field(date_str, run, fhour, product, fields, valid)
            return True
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass

    def _drain_backfill(self):
        """Service demand-driven backfill requests flagged by the frontend (404s). Claims
        pending rows, fetches each missing GFS-family field on demand, and marks the row
        done/failed. The render task then gap-fills the PNG on its next pass. Currents
        (RTOFS) is not serviced here — it uses a different run/hour mapping and the
        frontend reconciles its own hours — so such requests are marked failed."""
        claimed = self.db.claim_backfill_requests(limit=20)
        if not claimed:
            return
        gfs_base = self._gfs_base_url()
        cur_base = self._currents_base_url()
        for req in claimed:
            d, run, fhour, product = (
                req["run_date"],
                req["run_id"],
                int(req["fhour"]),
                req["product"],
            )
            d_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
            # Already present (raced with the normal cycle)? Mark done.
            if self.store.field_exists(d_str, run, fhour, product):
                self.db.mark_backfill(d_str, run, fhour, product, "done")
                continue
            try:
                ok = False
                if product in ATMOS_UNPACKERS:
                    if not gfs_base:
                        logger.warning("backfill: no 'gfs' datasource configured")
                    else:
                        ok = self._backfill_atmos_hour(
                            gfs_base,
                            d_str,
                            run,
                            fhour,
                            product,
                            ATMOS_UNPACKERS[product],
                        )
                elif product in WAVES_UNPACKERS:
                    if not gfs_base:
                        logger.warning("backfill: no 'gfs' datasource configured")
                    else:
                        ok = self._backfill_waves_hour(
                            gfs_base,
                            d_str,
                            run,
                            fhour,
                            product,
                            WAVES_UNPACKERS[product],
                        )
                elif product in CURRENTS_UNPACKERS:
                    if not cur_base:
                        logger.warning("backfill: no 'currents' datasource configured")
                    else:
                        ok = self._backfill_currents_hour(
                            cur_base,
                            d_str,
                            run,
                            fhour,
                            product,
                            CURRENTS_UNPACKERS[product],
                        )
                else:
                    logger.info(f"backfill: unknown product {product}; marking failed")
                    self.db.mark_backfill(d_str, run, fhour, product, "failed")
                    continue
                self.db.mark_backfill(
                    d_str, run, fhour, product, "done" if ok else "failed"
                )
                logger.info(
                    f"backfill {product} {d_str} {run}Z f{fhour:03d}: "
                    f"{'fetched' if ok else 'upstream missing -> failed'}"
                )
            except Exception as e:
                # Transient error: leave as failed (a later re-request resets to requested).
                logger.debug(f"backfill {product} f{fhour:03d} error: {e}")
                self.db.mark_backfill(d_str, run, fhour, product, "failed")

    def _collect_sst(self, base_url):
        """Download the yearly OISST netCDF (the mode the sst layer renders) into the
        shared file cache the sst updater reads, refreshing only when the remote is
        newer. SST is a single yearly netCDF, not a per-hour field, so it's a file cache
        rather than a stored fieldstore product."""
        sst_cfg = self.config.get_section("sst") or {}
        if not sst_cfg.get("enabled", False):
            logger.debug("Data Collector (sst): sst layer disabled; skipping download.")
            return
        mode = sst_cfg.get("mode", "absolute")
        url = build_oisst_url(base_url, mode)
        dest = oisst_cache_path(self.workdir, mode)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if not remote_is_newer(url, dest):
            logger.debug(
                f"Data Collector (sst): cache up to date ({os.path.basename(dest)})"
            )
            return
        try:
            logger.info(f"Data Collector (sst): downloading {url}")
            data = download_whole(url, timeout=300)
        except Exception as e:
            logger.error(f"Data Collector (sst): download failed: {e}")
            return
        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        logger.info(
            f"Data Collector (sst): wrote {len(data) / 1e6:.1f} MB -> "
            f"{os.path.basename(dest)}"
        )

    def _collect_clouds(self):
        """Fetch the global NASA GIBS cloud image into the shared cache the clouds layer
        reads. Gated on the clouds layer being enabled; refreshed only when the cache is
        older than expiry_hours. The date is the most recent complete day (now -
        offset_days) so VIIRS swaths are complete."""
        cfg = self.config.get_section("clouds") or {}
        if not cfg.get("enabled", False):
            logger.debug("Data Collector (clouds): clouds layer disabled; skipping.")
            return
        base_url = cfg.get("url")
        if not base_url:
            logger.warning("Data Collector (clouds): no clouds url configured; skipping.")
            return

        dest = clouds_cache_path(self.workdir)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # Refresh only if the cache is missing or older than expiry_hours.
        expiry_hours = float(cfg.get("expiry_hours", 3))
        if os.path.exists(dest):
            age_h = (time.time() - os.path.getmtime(dest)) / 3600.0
            if age_h < expiry_hours:
                logger.debug(
                    f"Data Collector (clouds): cache fresh ({age_h:.1f}h); skipping."
                )
                return

        # Dimensions from the global target geometry; date = now - offset_days.
        geom = self.config.get_setting("common", "target_geometry", "2048x1024")
        try:
            width, height = (int(x) for x in geom.lower().split("x"))
        except Exception:
            width, height = 2048, 1024
        offset_days = int(cfg.get("offset_days", 1))
        time_param = (
            datetime.now(timezone.utc) - timedelta(days=offset_days)
        ).strftime("%Y-%m-%d")
        layers = cfg.get("layers", "VIIRS_SNPP_CorrectedReflectance_TrueColor")
        url = build_clouds_url(base_url, width, height, time_param, layers=layers)

        try:
            logger.info(
                f"Data Collector (clouds): fetching GIBS {time_param} "
                f"({width}x{height})"
            )
            req = urllib.request.Request(
                url, headers={"User-Agent": "WorldMap-Cloud-Fetcher/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
        except Exception as e:
            logger.error(f"Data Collector (clouds): fetch failed: {e}")
            return
        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        logger.info(
            f"Data Collector (clouds): wrote {len(data) / 1e6:.1f} MB -> "
            f"{os.path.basename(dest)}"
        )

    def collect_once(self):
        for datasource, base_url in self.datasources.items():
            try:
                if datasource == "gfs":
                    self._collect_gfs_atmos(base_url.rstrip("/"))
                    self._collect_gfs_waves(base_url.rstrip("/"))
                elif datasource == "currents":
                    self._collect_rtofs_currents(base_url.rstrip("/"))
                elif datasource == "sst":
                    self._collect_sst(base_url.rstrip("/"))
                else:
                    logger.error(f"unknown datasource {datasource}")
            except Exception as e:
                logger.error(f"datasource {datasource} failed: {e}")
        # Clouds' endpoint lives in the clouds layer config (not datasources); fetch it
        # each cycle, gated internally on the layer being enabled.
        try:
            self._collect_clouds()
        except Exception as e:
            logger.error(f"datasource clouds failed: {e}")

    async def run(self):
        # Two cadences: the heavy full refresh runs every update_period_s (set via
        # update_minutes, or legacy update_hours); a light backfill drain runs every
        # backfill_poll_seconds (default 60) so frontend-flagged missing data fills within
        # ~a minute rather than waiting for the next full cycle.
        poll_s = int(self.settings.get("backfill_poll_seconds", 60))
        last_full = None  # None => run a full refresh immediately on first iteration
        while True:
            self.refresh_settings()  # recomputes self.update_period_s, cache_hours, etc.
            poll_s = int(self.settings.get("backfill_poll_seconds", poll_s))
            full_period = self.update_period_s
            enabled = self.settings.get("enabled", False)
            now = asyncio.get_event_loop().time()

            if enabled and (last_full is None or (now - last_full) >= full_period):
                logger.info("Data Collector: refreshing datasets")
                try:
                    self.collect_once()
                except Exception as e:
                    logger.error(f"Data Collector cycle failed: {e}")
                last_full = now
            elif not enabled:
                logger.debug("Data Collector disabled. Skipping full refresh.")

            # Backfill drain runs every poll regardless of the full-refresh timer (still
            # gated on enabled, so a disabled collector does nothing).
            if enabled:
                try:
                    self._drain_backfill()
                except Exception as e:
                    logger.error(f"backfill drain failed: {e}")

            await asyncio.sleep(max(5, poll_s))


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(DataCollector(args.config).run())


if __name__ == "__main__":
    main()
