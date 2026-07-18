#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-15).
"""GfsWavesCollector — the per-hour GFS-Wave global 0p25 swell field, extracted from the
now-deleted field_ingest.py's _collect_gfs_waves as the second per-source
FieldCollectorBase subclass (Phase 3, complete).

A SingleFileFieldCollector (field_base.py): whole-file per-hour download, single
product, no fallback when the forecast hour isn't published yet (unlike
RtofsCurrentsCollector, which falls back to a nowcast) -- so collect()/backfill_hour()
are inherited; only _resolve_download_url() and the baseline/tempfile settings are
this source's own.

Runs on the SAME GFS run + forecast-hour cadence as GfsAtmosCollector, so it shares the
baseline (CycleContext key "gfs") rather than probing NOMADS a second time — see
field_base.CycleContext.
"""
import logging

from atmos_gl.lib.gfs import (
    resolve_gfs_baseline_with_coverage,
    remote_exists,
    build_wave_url,
)
from atmos_gl.lib.unpack import WAVES_UNPACKERS
from .field_base import SingleFileFieldCollector

logger = logging.getLogger("atmos_gl.collectors.gfs_waves")


class GfsWavesCollector(SingleFileFieldCollector):
    status_name = "gfs_waves"
    # See GfsAtmosCollector.display_label -- same reason (status_name isn't a real
    # config section, so the generic derivation would give "Gfs Waves").
    display_label = "GFS Waves"
    datasource_key = "gfs"
    baseline_key = "gfs"
    channel_key = "gfs_waves"
    products = WAVES_UNPACKERS
    tempfile_suffix = ".grib2"
    cleanup_idx = True

    def resolve_baseline(self, base_url: str):
        return resolve_gfs_baseline_with_coverage(base_url, self.cache_hours)

    def _resolve_download_url(
        self, base_url, run_date_str, run_id, fhour, *, allow_fallback
    ):
        url = build_wave_url(base_url, run_date_str, run_id, fhour)
        if not remote_exists(url):
            logger.debug(f"waves f{fhour:03d}: not published yet")
            return None
        return url
