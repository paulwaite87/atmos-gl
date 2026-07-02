#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""GfsAtmosCollector — atmospheric pgrb2.0p25 byte-range union (isobars/precip/temperature/
ozone/wind/stormwatch), extracted from the now-deleted field_ingest.py's _collect_gfs_atmos
as the first per-source FieldCollectorBase subclass (Phase 3, complete).

Shares its baseline (CycleContext key "gfs") with GfsWavesCollector, which needs the same GFS
run — see field_base.CycleContext.
"""
import os
import glob
import logging
import tempfile
from datetime import datetime, timedelta, timezone

from worldmap.lib.gfs import (
    ATMOS_TARGETS,
    resolve_gfs_baseline_with_coverage,
    gfs_index_ranges,
    download_byte_ranges,
    build_atmos_url,
)
from worldmap.lib.unpack import ATMOS_UNPACKERS
from .field_base import FieldCollectorBase, CycleContext

logger = logging.getLogger("worldmap.collectors.gfs_atmos")


class GfsAtmosCollector(FieldCollectorBase):
    datasource_key = "gfs"
    baseline_key = "gfs"
    products = ATMOS_UNPACKERS

    def resolve_baseline(self, base_url: str):
        return resolve_gfs_baseline_with_coverage(base_url, self.cache_hours)

    def collect(self, ctx: CycleContext) -> None:
        base_url = self.base_url()
        if not base_url:
            logger.warning("gfs_atmos: no 'gfs' datasource configured")
            return

        baseline = ctx.baseline(self.baseline_key, lambda: self.resolve_baseline(base_url))
        if not baseline:
            logger.warning("Data Collector: could not resolve a GFS baseline; will retry.")
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

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch a single atmos product for one (date, run, hour) via the byte-range path,
        mirroring collect()'s inner body for exactly one hour/product."""
        base_url = self.base_url()
        unpacker = ATMOS_UNPACKERS[product]
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
