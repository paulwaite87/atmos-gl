#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-15).
"""RtofsCurrentsCollector — RTOFS daily-run hourly surface u/v (to f072), extracted from the
now-deleted field_ingest.py's _collect_rtofs_currents as the third and final per-source
FieldCollectorBase subclass (Phase 3, complete).

A SingleFileFieldCollector (field_base.py): whole-file per-hour download, single
product -- so collect()/backfill_hour() are inherited. This source overrides three
hooks beyond the baseline/tempfile settings: _expected_fhour_end() (the f072 hourly
cap), _guard_cycle() (abort once the run is too old for that cap), and
_resolve_download_url() (the nowcast fallback -- see its docstring for the
collect()-vs-backfill_hour() distinction).

Uses its own baseline (CycleContext key "rtofs") since it's a different model on a
different cadence to the GFS pair — no probe is shared here, but the same per-cycle
memoisation still applies if something else ever needs the RTOFS baseline too. See
field_base.CycleContext.
"""
import logging

from atmos_gl.lib.rtofs import (
    resolve_rtofs_baseline,
    build_currents_url,
    build_currents_nowcast_url,
    RTOFS_MAX_HOURLY_FHOUR,
)
from atmos_gl.lib.gfs import remote_exists
from atmos_gl.lib.unpack import CURRENTS_UNPACKERS
from .field_base import SingleFileFieldCollector

logger = logging.getLogger("atmos_gl.collectors.rtofs_currents")


class RtofsCurrentsCollector(SingleFileFieldCollector):
    status_name = "rtofs_currents"
    datasource_key = "currents"
    baseline_key = "rtofs"
    channel_key = "rtofs_currents"
    products = CURRENTS_UNPACKERS
    tempfile_suffix = ".nc"

    def resolve_baseline(self, base_url: str):
        return resolve_rtofs_baseline(base_url)

    def _expected_fhour_end(self, fhour_0: int) -> int:
        """RTOFS surface files are hourly only to f072; collect()'s window and
        data_status()'s expectation must share this ceiling or collect() would request
        non-existent (3-hourly) hours and data_status() would report < 100% forever."""
        return min(fhour_0 + self.cache_hours, RTOFS_MAX_HOURLY_FHOUR + 1)

    def _guard_cycle(self, fhour_0: int, fhour_end: int) -> bool:
        """Abort the cycle once the run is older than RTOFS's hourly limit -- otherwise
        _expected_fhour_end's cap alone would silently produce an empty (and
        increasingly backwards-looking) window with no explanation in the logs."""
        if fhour_0 > RTOFS_MAX_HOURLY_FHOUR:
            logger.warning(
                f"RTOFS run is {fhour_0}h old (> {RTOFS_MAX_HOURLY_FHOUR}h hourly "
                f"limit); a newer run should appear shortly."
            )
            return False
        return True

    def _resolve_download_url(
        self, base_url, run_date_str, run_id, fhour, *, allow_fallback
    ):
        """Forecast-hour file, falling back to the nowcast (present conditions) if not
        yet published. `allow_fallback` is False for every collect()-loop hour after
        fhour_0 (later hours should wait for their own forecast, not silently reuse
        'now') but always True from backfill_hour() (a backfill request is for one
        specific missing hour, so a nowcast is still better than nothing for it)."""
        url = build_currents_url(base_url, run_date_str, fhour)
        if remote_exists(url):
            return url
        fallback = build_currents_nowcast_url(base_url, run_date_str)
        if allow_fallback and remote_exists(fallback):
            logger.debug(f"currents f{fhour:03d} missing; using n000 nowcast")
            return fallback
        logger.debug(f"currents f{fhour:03d}: not published yet")
        return None
