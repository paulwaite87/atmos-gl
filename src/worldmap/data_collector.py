#!/usr/bin/env python3
import os
import glob
import logging
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel
from worldmap.lib import fieldstore
from worldmap.lib.gfs import (
    ATMOS_TARGETS,
    resolve_gfs_baseline,
    gfs_index_ranges,
    download_byte_ranges,
    download_whole,
    remote_exists,
    build_atmos_url,
)
from worldmap.lib.rtofs import (
    resolve_rtofs_baseline,
    build_currents_url,
    build_currents_nowcast_url,
    RTOFS_MAX_HOURLY_FHOUR,
)
from worldmap.lib.unpack import ATMOS_UNPACKERS, CURRENTS_UNPACKERS

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
        self.store = fieldstore.get_store(workdir, db=self.db)
        logger.debug("Initializing Data Collector")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("data_collector")
        self.datasources = self.settings.get("datasources", {})
        self.update_hours = int(self.settings.get("update_hours", 12))
        self.cache_hours = int(self.settings.get("cache_hours", 24))
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)

    # -- GFS atmospheric union ------------------------------------------------
    def _collect_gfs_atmos(self, base_url):
        baseline = resolve_gfs_baseline(base_url)
        if not baseline:
            logger.warning(
                "Data Collector: could not resolve a GFS baseline; will retry."
            )
            return

        gfs_date_str, gfs_run, gfs_timestamp = (
            baseline["date_str"],
            baseline["run"],
            baseline["timestamp"],
        )
        now = datetime.now(timezone.utc)
        hours_since_run = int(round((now - gfs_timestamp).total_seconds() / 3600.0))
        fhour_0 = max(0, hours_since_run)  # forecast hour valid 'now' (no user offset)
        fhour_end = fhour_0 + self.cache_hours

        products = list(ATMOS_UNPACKERS.items())
        stored = 0

        for fhour in range(fhour_0, fhour_end):
            valid = gfs_timestamp + timedelta(hours=fhour)

            # Which products still need this hour? Skip the download entirely if none.
            missing = [
                (product, unpacker)
                for (product, unpacker) in products
                if not self.store.field_exists(gfs_date_str, gfs_run, fhour, product)
            ]
            if not missing:
                continue

            aurl = build_atmos_url(base_url, gfs_date_str, gfs_run, fhour)
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
                            gfs_date_str, gfs_run, fhour, product, fields, valid
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
            f"Data Collector (gfs): {gfs_date_str} {gfs_run}Z, hours {fhour_0:03d}..{fhour_end - 1:03d}; "
            f"stored {stored} field(s)."
        )
        try:
            self.store.prune_except_run(
                gfs_date_str, gfs_run, products=list(ATMOS_UNPACKERS.keys())
            )
        except Exception as e:
            logger.debug(f"prune skipped: {e}")

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
    def collect_once(self):
        for datasource, base_url in self.datasources.items():
            try:
                if datasource == "gfs":
                    self._collect_gfs_atmos(base_url.rstrip("/"))
                    # TODO: GFS wave product -> waves_data_unpack -> store_field(..., "waves")
                elif datasource == "currents":
                    self._collect_rtofs_currents(base_url.rstrip("/"))
                elif datasource == "sst":
                    pass  # TODO: OISST -> sst_data_unpack
                else:
                    logger.error(f"unknown datasource {datasource}")
            except Exception as e:
                logger.error(f"datasource {datasource} failed: {e}")

    async def run(self):
        while True:
            self.refresh_settings()
            if self.settings.get("enabled", False):
                logger.info("Data Collector: refreshing datasets")
                try:
                    self.collect_once()
                except Exception as e:
                    logger.error(f"Data Collector cycle failed: {e}")
            else:
                logger.debug("Data Collector disabled. Skipping.")
            await asyncio.sleep(self.update_hours * 3600)


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
