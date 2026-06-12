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
from worldmap.lib.gfs import (
    ATMOS_TARGETS,
    resolve_gfs_baseline,
    gfs_index_ranges,
    download_byte_ranges,
    build_atmos_url,
)
from worldmap.lib.unpack import ATMOS_UNPACKERS

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
            logger.warning("Data Collector: could not resolve a GFS baseline; will retry.")
            return

        date_str, run, ts = baseline["date_str"], baseline["run"], baseline["timestamp"]
        now = datetime.now(timezone.utc)
        hours_since_run = int(round((now - ts).total_seconds() / 3600.0))
        f0 = max(0, hours_since_run)        # forecast hour valid 'now' (no user offset)
        f_end = f0 + self.cache_hours

        self.db.ensure_layer_data_table()
        products = list(ATMOS_UNPACKERS.items())
        stored = 0

        for f in range(f0, f_end):
            valid = ts + timedelta(hours=f)

            # Which products still need this hour? Skip the download entirely if none.
            missing = [(p, fn) for (p, fn) in products
                       if not self.db.field_exists(date_str, run, f, p)]
            if not missing:
                continue

            aurl = build_atmos_url(base_url, date_str, run, f)
            try:
                ranges = gfs_index_ranges(aurl, ATMOS_TARGETS)
                if not ranges:
                    logger.debug(f"atmos f{f:03d}: index not ready yet")
                    continue
                data = download_byte_ranges(aurl, ranges)
                if not data:
                    continue
            except Exception as e:
                logger.debug(f"atmos f{f:03d} download skipped: {e}")
                continue

            tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
            tmp.write(data)
            tmp.close()
            try:
                for product, unpack in missing:
                    try:
                        fields = unpack(tmp.name)
                        self.db.store_field(date_str, run, f, product, fields, valid)
                        stored += 1
                    except Exception as e:
                        logger.debug(f"{product} f{f:03d} unpack/store failed: {e}")
            finally:
                # Remove the temp GRIB and any cfgrib .idx sidecars it created.
                for path in [tmp.name] + glob.glob(tmp.name + "*.idx"):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        logger.info(
            f"Data Collector (gfs): {date_str} {run}Z, hours {f0:03d}..{f_end - 1:03d}; "
            f"stored {stored} field(s)."
        )
        try:
            self.db.prune_layer_data_except(date_str, run)
        except Exception as e:
            logger.debug(f"prune skipped: {e}")

    # -- dispatch -------------------------------------------------------------
    def collect_once(self):
        for datasource, base_url in self.datasources.items():
            try:
                if datasource == "gfs":
                    self._collect_gfs_atmos(base_url.rstrip("/"))
                    # TODO: GFS wave product -> waves_data_unpack -> store_field(..., "waves")
                elif datasource == "currents":
                    pass   # TODO: RTOFS -> currents_data_unpack
                elif datasource == "sst":
                    pass   # TODO: OISST -> sst_data_unpack
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
