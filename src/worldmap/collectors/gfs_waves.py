#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""GfsWavesCollector — the per-hour GFS-Wave global 0p25 swell field, extracted from the
now-deleted field_ingest.py's _collect_gfs_waves as the second per-source
FieldCollectorBase subclass (Phase 3, complete).

Runs on the SAME GFS run + forecast-hour cadence as GfsAtmosCollector, so it shares the
baseline (CycleContext key "gfs") rather than probing NOMADS a second time — see
field_base.CycleContext.
"""
import os
import glob
import logging
import tempfile
from datetime import datetime, timedelta, timezone

from worldmap.lib.gfs import (
    resolve_gfs_baseline_with_coverage,
    download_whole,
    remote_exists,
    build_wave_url,
)
from worldmap.lib.unpack import WAVES_UNPACKERS
from .field_base import FieldCollectorBase, CycleContext

logger = logging.getLogger("worldmap.collectors.gfs_waves")


class GfsWavesCollector(FieldCollectorBase):
    datasource_key = "gfs"
    baseline_key = "gfs"
    products = WAVES_UNPACKERS

    def resolve_baseline(self, base_url: str):
        return resolve_gfs_baseline_with_coverage(base_url, self.cache_hours)

    def collect(self, ctx: CycleContext) -> None:
        base_url = self.base_url()
        if not base_url:
            logger.warning("gfs_waves: no 'gfs' datasource configured")
            return

        baseline = ctx.baseline(self.baseline_key, lambda: self.resolve_baseline(base_url))
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

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch the GFS-Wave global 0p25 GRIB for one hour (whole-file), mirroring
        collect()'s inner body for exactly one hour."""
        base_url = self.base_url()
        unpacker = WAVES_UNPACKERS[product]
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
