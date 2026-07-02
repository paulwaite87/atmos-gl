#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""RtofsCurrentsCollector — RTOFS daily-run hourly surface u/v (to f072), extracted from the
now-deleted field_ingest.py's _collect_rtofs_currents as the third and final per-source
FieldCollectorBase subclass (Phase 3, complete).

Uses its own baseline (CycleContext key "rtofs") since it's a different model on a
different cadence to the GFS pair — no probe is shared here, but the same per-cycle
memoisation still applies if something else ever needs the RTOFS baseline too. See
field_base.CycleContext.
"""
import os
import logging
import tempfile
from datetime import datetime, timedelta, timezone

from worldmap.lib.rtofs import (
    resolve_rtofs_baseline,
    build_currents_url,
    build_currents_nowcast_url,
    RTOFS_MAX_HOURLY_FHOUR,
)
from worldmap.lib.gfs import download_whole, remote_exists
from worldmap.lib.unpack import CURRENTS_UNPACKERS
from .field_base import FieldCollectorBase, CycleContext

logger = logging.getLogger("worldmap.collectors.rtofs_currents")


class RtofsCurrentsCollector(FieldCollectorBase):
    status_name = "rtofs_currents"
    datasource_key = "currents"
    baseline_key = "rtofs"
    products = CURRENTS_UNPACKERS

    def resolve_baseline(self, base_url: str):
        return resolve_rtofs_baseline(base_url)

    def _expected_fhour_end(self, fhour_0: int) -> int:
        """RTOFS surface files are hourly only to f072 (see collect()'s own window cap);
        data_status() must expect the same ceiling or it would report < 100% forever
        once the window runs past what RTOFS could ever publish hourly."""
        return min(fhour_0 + self.cache_hours, RTOFS_MAX_HOURLY_FHOUR + 1)

    def collect(self, ctx: CycleContext) -> None:
        base_url = self.base_url()
        if not base_url:
            logger.warning("rtofs_currents: no 'currents' datasource configured")
            return

        baseline = ctx.baseline(self.baseline_key, lambda: self.resolve_baseline(base_url))
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

        # RTOFS surface files are hourly only to f072; cap the cache window so the simple
        # hourly loop never requests a non-existent (3-hourly) hour.
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
            # Fall back to the nowcast (present conditions) if this forecast hour isn't
            # published yet; better a current 'now' field than a gap.
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

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch a single RTOFS currents hour on demand. RTOFS URLs key off date + fhour (one
        daily cycle), with the nowcast as a fallback when the forecast hour isn't published
        — unconditionally here (unlike collect()'s window loop, which only falls back for
        fhour_0): a backfill request is for one specific missing hour, so a nowcast is still
        better than nothing for it."""
        base_url = self.base_url()
        unpacker = CURRENTS_UNPACKERS[product]
        url = build_currents_url(base_url, run_date, fhour)
        if not remote_exists(url):
            fallback = build_currents_nowcast_url(base_url, run_date)
            if remote_exists(fallback):
                url = fallback
            else:
                return False
        data = download_whole(url)
        if not data:
            return False
        valid = self._valid_time(run_date, run_id, fhour)
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
        tmp.write(data)
        tmp.close()
        try:
            fields = unpacker(tmp.name)
            self.store.store_field(run_date, run_id, fhour, product, fields, valid)
            return True
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
