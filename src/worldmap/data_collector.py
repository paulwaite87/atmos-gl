#!/usr/bin/env python3
import logging
import asyncio
from datetime import datetime, timedelta, timezone

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel
from worldmap.lib.gfs import (
    ATMOS_TARGETS,
    resolve_gfs_baseline,
    gfs_index_ranges,
    download_byte_ranges,
    download_whole,
    remote_exists,
    build_atmos_url,
    build_wave_url,
)

logger = logging.getLogger("worldmap.data_collector")


class DataCollector:
    """Background process that pre-fetches whole hours of data into the database.

    Each cycle it finds the newest GFS run, then for forecast hours from 'now' forward
    (cache_hours of them) it downloads two products and stores them keyed by
    (date, run, fhour, product):

      * 'atmos' - one ranged download of the union of every atmospheric layer's targets
                  (isobars/wind/precip/ozone/stormwatch/temperature) from the shared
                  pgrb2.0p25 file.
      * 'wave'  - the GFS wave gridded GRIB.

    Tasks then read their slice from the DB instead of downloading. (Currents=RTOFS and
    SST=OISST are not GFS and are intentionally out of scope here.)
    """

    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        logger.debug("Initializing GFS Collector")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("data_collector")
        self.datasources = self.settings.get("datasources", {})
        self.update_hours = int(self.settings.get("update_hours", 12))
        self.cache_hours = int(self.settings.get("cache_hours", 24))
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)

    def collect_once(self):
        for datasource, base_url in self.datasources.items():
            if datasource == "sst":
                pass
            elif datasource == "currents":
                pass
            elif datasource == "gfs":
                baseline = resolve_gfs_baseline(base_url)
                if not baseline:
                    logger.warning("GFS Collector: could not resolve a GFS baseline; will retry.")
                    return

                date_str, run, ts = baseline["date_str"], baseline["run"], baseline["timestamp"]
                now = datetime.now(timezone.utc)
                hours_since_run = int(round((now - ts).total_seconds() / 3600.0))
                f0 = max(0, hours_since_run)        # forecast hour valid 'now' (no user offset)
                f_end = f0 + self.cache_hours

                atmos_new = wave_new = 0
                for f in range(f0, f_end):
                    valid = ts + timedelta(hours=f)

                    # --- atmospheric union (one ranged download for six layers) ---
                    try:
                        if not self.db.gfs_grib_exists(date_str, run, f, "atmos"):
                            aurl = build_atmos_url(base_url, date_str, run, f)
                            ranges = gfs_index_ranges(aurl, ATMOS_TARGETS)
                            if ranges:
                                data = download_byte_ranges(aurl, ranges)
                                if data:
                                    self.db.store_gfs_grib(date_str, run, f, "atmos", data, valid)
                                    atmos_new += 1
                            else:
                                logger.debug(f"atmos f{f:03d}: index not ready yet")
                    except Exception as e:
                        logger.debug(f"atmos f{f:03d} skipped: {e}")

                    # --- wave product (whole file) ---
                    try:
                        if not self.db.gfs_grib_exists(date_str, run, f, "wave"):
                            wurl = build_wave_url(base_url, date_str, run, f)
                            if remote_exists(wurl):
                                wdata = download_whole(wurl)
                                if wdata:
                                    self.db.store_gfs_grib(date_str, run, f, "wave", wdata, valid)
                                    wave_new += 1
                            else:
                                logger.debug(f"wave f{f:03d}: not published yet")
                    except Exception as e:
                        logger.debug(f"wave f{f:03d} skipped: {e}")

                logger.info(
                    f"GFS Collector: {date_str} {run}Z, hours {f0:03d}..{f_end - 1:03d}; "
                    f"stored {atmos_new} atmos + {wave_new} wave new blob(s)."
                )

                # Drop any superseded runs so the cache only holds the current one.
                try:
                    self.db.prune_gfs_cache_except(date_str, run)
                except Exception as e:
                    logger.debug(f"prune skipped: {e}")
            else:
                logger.error(f"unknown datasource {datasource}")

    async def run(self):
        while True:
            self.refresh_settings()
            if self.settings.get("enabled", False):
                logger.info("GFS Collector: refreshing GFS dataset")
                try:
                    self.collect_once()
                except Exception as e:
                    logger.error(f"GFS Collector cycle failed: {e}")
            else:
                logger.debug("GFS Collector disabled. Skipping.")
            await asyncio.sleep(self.update_hours * 3600)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(GFSCollector(args.config).run())


if __name__ == "__main__":
    main()