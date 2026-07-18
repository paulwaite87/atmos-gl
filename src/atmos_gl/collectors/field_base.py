#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""FieldCollectorBase + CycleContext — shared scaffolding for the GFS/RTOFS forecast-field
collectors (gfs_atmos.py, gfs_waves.py, rtofs_currents.py — Phase 3, complete).

SingleFileFieldCollector(FieldCollectorBase) further shares a concrete collect()/
backfill_hour() between GfsWavesCollector and RtofsCurrentsCollector -- both fetch one
whole file per forecast hour for a single product, differing only in URL resolution
(plus fallback), tempfile suffix, and (for RTOFS) an f072 window cap/abort. See its
class docstring below. GfsAtmosCollector's multi-product byte-range fetch stays its own
implementation, subclassing FieldCollectorBase directly.

CollectorBase's is_stale/has_new_data scheduling contract doesn't fit these sources: they
aren't polled on their own cadence, they're driven every full-refresh cycle (already gated by
data_collector.enabled + runs_per_day at the service level) and self-gate per forecast hour
via fieldstore.field_exists(). What they DO need from CollectorBase is the config/db/settings
plumbing, so FieldCollectorBase stays a subclass but replaces the per-source scheduling with:

  * a fieldstore handle (self.store), bound at construction (constructed fresh each cycle
    by CollectorService._collect_fields())
  * self.base_url()   — the configured datasources{} URL for this source
  * self.cache_hours  — the shared data_collector.cache_hours window
  * collect(ctx)      — takes a CycleContext, unlike CollectorBase's bare collect()

CycleContext exists because GfsAtmosCollector and GfsWavesCollector both need the SAME GFS
run baseline. Whoever drives the field collectors each full-refresh pass constructs ONE
CycleContext and passes it to every collect(ctx) call; the second collector asking for a
given baseline key gets the memoised result instead of a second NOMADS probe.

drain_backfill() is the generic counterpart to collectors/__init__.py's _drive(): where
_drive() drives CollectorBase subclasses through is_stale/has_new_data/collect(), this drives
FieldCollectorBase subclasses through the frontend-flagged backfill queue, routing each
claimed (run_date, run_id, fhour, product) request to whichever collector's `products` dict
owns that product. Adding a new source's backfill support is then "add its class to the
list", not a new branch here.
"""
import glob
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .base import CollectorBase
from atmos_gl.lib.data_status import (
    estimate_next_update,
    period_s_from_runs_per_day,
    read_process_status,
    resolve_run_epoch_utc,
    build_status,
)
from atmos_gl.lib.gfs import download_whole

logger = logging.getLogger(__name__)


@contextmanager
def with_tempfile(data: bytes, suffix: str, cleanup_idx: bool = False):
    """Write `data` to a fresh tempfile and yield its path; always remove it afterwards
    (plus any `*.idx` cfgrib sidecars, if `cleanup_idx`), even if the body raises.

    Owns only the tempfile's lifecycle — not the download (already source-specific in
    lib/gfs.py/lib/rtofs.py) or the unpack/store/error-handling that follows. For
    GfsWavesCollector/RtofsCurrentsCollector that surrounding mechanic is now shared via
    SingleFileFieldCollector, below; GfsAtmosCollector still calls this directly since
    it unpacks several products from one tempfile, each with its own try/except --
    genuinely different from the other two's single-product case, not copy-paste.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(data)
    tmp.close()
    try:
        yield tmp.name
    finally:
        paths = [tmp.name] + (glob.glob(tmp.name + "*.idx") if cleanup_idx else [])
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass


class CycleContext:
    """Per-cycle memoisation of model baselines (GFS run, RTOFS run).

    `resolver` is a zero-arg callable; its result — including None, when the baseline can't
    be resolved this cycle — is cached under `key` for the life of this context, so a second
    call for the same key within the same cycle is free. Construct a fresh CycleContext for
    each full-refresh pass; reusing one across cycles would pin a stale (or failed) baseline
    past the point where retrying could succeed.
    """

    def __init__(self):
        self._baselines: dict = {}
        self._resolved: set = set()

    def baseline(self, key: str, resolver):
        if key not in self._resolved:
            self._baselines[key] = resolver()
            self._resolved.add(key)
        return self._baselines[key]


class FieldCollectorBase(CollectorBase):
    """Base for GFS/RTOFS forecast-field collectors.

    Subclasses set `datasource_key` (the key into data_collector.datasources, e.g. "gfs" or
    "currents") and `baseline_key` (the CycleContext memoisation key — GfsAtmosCollector and
    GfsWavesCollector both use "gfs"; RtofsCurrentsCollector uses "rtofs"), and implement
    resolve_baseline(base_url) and collect(ctx).

    `section` is "data_collector" for ALL THREE subclasses (they share that config section),
    so it can't double as a per-collector identity the way it does for CollectorBase's other
    subclasses. `status_name` is that identity — a unique key for process_status rows and the
    Data Status API, set per subclass (e.g. "gfs_atmos"). Never use `section` for that.
    """

    section = "data_collector"
    status_name: str = ""  # override in every subclass — unique process_status key
    datasource_key: str = ""  # override in every subclass
    baseline_key: str = ""  # override in every subclass
    products: dict = {}  # override: {product_name: unpacker} this collector owns

    def __init__(self, config, store):
        super().__init__(config)
        self.store = store

    @property
    def cache_hours(self) -> int:
        return int(self.settings.get("cache_hours", 24))

    def base_url(self) -> str | None:
        """The configured base URL for this source's datasource_key, or None if the operator
        hasn't configured one for this datasource."""
        bu = self.settings.get("datasources", {}).get(self.datasource_key)
        return bu.rstrip("/") if bu else None

    def resolve_baseline(self, base_url: str) -> dict | None:
        """Probe the source for its current run baseline — {date_str, run, timestamp} — or
        None if it can't be resolved right now. Override per source; this is the callable
        CycleContext.baseline() memoises."""
        raise NotImplementedError(
            f"{type(self).__name__}.resolve_baseline() not implemented"
        )

    def collect(self, ctx: CycleContext) -> None:
        """One full pass for this source. Deliberately takes `ctx` — unlike
        CollectorBase.collect(self) — so the baseline probe can be shared via
        ctx.baseline(self.baseline_key, lambda: self.resolve_baseline(base_url)). Override in
        every subclass."""
        raise NotImplementedError(f"{type(self).__name__}.collect() not implemented")

    def _expected_fhour_end(self, fhour_0: int) -> int:
        """The forecast hour (exclusive) the current window is expected to reach, given
        fhour_0 (the hour valid 'now'). Default: the full cache_hours window. Override to
        cap it (RtofsCurrentsCollector caps at its hourly-only-to-f072 limit, matching
        collect()'s own window logic) so data_status() never expects hours the source
        could never have published."""
        return fhour_0 + self.cache_hours

    def _service_period_s(self) -> float:
        """How often CollectorService._collect_fields() runs (data_collector.runs_per_day)
        — mirrors CollectorService.refresh_settings's own formula so data_status()'s
        next_update matches the real cadence."""
        return period_s_from_runs_per_day(self.settings.get("runs_per_day", 96))

    def data_status(self) -> dict:
        """Coverage-based override of CollectorBase.data_status(): percent is the fraction
        of this collector's expected (product x forecast-hour) cells actually present for
        the freshest run already in field_catalog — deliberately NOT a live NOMADS/RTOFS
        re-probe (data_status() must be cheap and side-effect-free; a status check
        shouldn't itself hit rate limits). last_updated/detail still come from
        process_status (written by CollectorService._collect_fields()), same as the
        CollectorBase default."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.status_name
        )

        products = list(self.products.keys())
        avail = (
            self.store.field_catalog_adapter.get_latest_run_hours(products=products)
            if products
            else None
        )
        percent = 0.0
        detail = last_error
        if avail and avail.get("hours"):
            run_date, run_id, hours = (
                avail["run_date"],
                avail["run_id"],
                avail["hours"],
            )
            run_date_str = (
                run_date.isoformat() if hasattr(run_date, "isoformat") else str(run_date)
            )
            run_ts = self._valid_time(run_date_str, run_id, 0)
            now = datetime.now(timezone.utc)
            fhour_0 = max(0, int(round((now - run_ts).total_seconds() / 3600.0)))
            fhour_end = self._expected_fhour_end(fhour_0)
            expected_total = max(0, fhour_end - fhour_0)
            present = sum(1 for h in hours if fhour_0 <= h < fhour_end)
            percent = 100.0 * present / expected_total if expected_total > 0 else 0.0
            if not detail:
                detail = f"{run_date_str} {run_id}Z: {present}/{expected_total} hour(s)"

        period_s = self._service_period_s()
        return build_status(
            name=self.status_name,
            kind="collector",
            percent=percent,
            last_updated=last_updated,
            next_update=estimate_next_update(last_updated, period_s, self.enabled),
            enabled=self.enabled,
            detail=detail,
            status=status,
        )

    @staticmethod
    def _valid_time(run_date: str, run_id: str, fhour) -> datetime:
        return resolve_run_epoch_utc(run_date, run_id) + timedelta(hours=int(fhour))

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch + store a single (run_date, run_id, fhour, product) hour on demand, for the
        frontend-flagged backfill queue (drain_backfill(), below). Returns True if the field
        was fetched and stored, False if upstream doesn't have it. Override per source."""
        raise NotImplementedError(
            f"{type(self).__name__}.backfill_hour() not implemented"
        )


class SingleFileFieldCollector(FieldCollectorBase):
    """FieldCollectorBase subclass for sources that fetch one whole file per forecast
    hour for a single product -- GfsWavesCollector, RtofsCurrentsCollector -- as opposed
    to GfsAtmosCollector's multi-product byte-range fetch, which stays its own
    collect()/backfill_hour() implementation (the two shapes differ on two axes at
    once: product cardinality per fetch, and byte-range vs whole-file download).

    Subclasses set `tempfile_suffix` (and `cleanup_idx` if GRIB .idx sidecars need
    sweeping) and implement `_resolve_download_url()`; override `_guard_cycle()` and/or
    `_expected_fhour_end()` only if a source needs to cap or abort its window, as
    RtofsCurrentsCollector does for its f072 hourly limit.
    """

    tempfile_suffix: str = ""  # override in every subclass -- e.g. ".grib2", ".nc"
    cleanup_idx: bool = False  # override to True for GRIB sources with .idx sidecars

    def _guard_cycle(self, fhour_0: int, fhour_end: int) -> bool:
        """Called once before collect()'s per-hour loop; return False (having logged
        why) to abort the whole cycle. Default: always proceed. Override when a
        source's window can run past what upstream could ever have published."""
        return True

    def _resolve_download_url(
        self,
        base_url: str,
        run_date_str: str,
        run_id: str,
        fhour: int,
        *,
        allow_fallback: bool,
    ) -> str | None:
        """Return the URL to fetch for this (run, hour), or None if unavailable this
        cycle. Encapsulates the remote-exists check and any fallback (e.g. RTOFS's
        nowcast) per source. `allow_fallback` is True unconditionally from
        backfill_hour() -- a backfill request is for one specific missing hour, so any
        fallback is better than nothing -- but only for fhour == fhour_0 from
        collect()'s window loop, since later hours should wait for their own forecast
        rather than silently reuse 'now'. Override per source."""
        raise NotImplementedError(
            f"{type(self).__name__}._resolve_download_url() not implemented"
        )

    def collect(self, ctx: CycleContext) -> None:
        base_url = self.base_url()
        if not base_url:
            logger.warning(
                f"{self.status_name}: no '{self.datasource_key}' datasource configured"
            )
            return

        baseline = ctx.baseline(self.baseline_key, lambda: self.resolve_baseline(base_url))
        if not baseline:
            logger.warning(
                f"Data Collector: could not resolve a {self.baseline_key} baseline for "
                f"{self.status_name}; will retry."
            )
            return

        run_date_str, run_id, run_timestamp = (
            baseline["date_str"],
            baseline["run"],
            baseline["timestamp"],
        )
        now = datetime.now(timezone.utc)
        fhour_0 = max(0, int(round((now - run_timestamp).total_seconds() / 3600.0)))
        fhour_end = self._expected_fhour_end(fhour_0)

        if not self._guard_cycle(fhour_0, fhour_end):
            return

        product, unpacker = next(iter(self.products.items()))
        stored = 0

        for fhour in range(fhour_0, fhour_end):
            if self.store.field_exists(run_date_str, run_id, fhour, product):
                continue

            url = self._resolve_download_url(
                base_url, run_date_str, run_id, fhour, allow_fallback=(fhour == fhour_0)
            )
            if not url:
                continue

            valid = run_timestamp + timedelta(hours=fhour)
            try:
                data = download_whole(url)
                if not data:
                    continue
            except Exception as e:
                logger.debug(f"{self.status_name} f{fhour:03d} download skipped: {e}")
                continue

            try:
                with with_tempfile(
                    data, self.tempfile_suffix, cleanup_idx=self.cleanup_idx
                ) as tmp_path:
                    fields = unpacker(tmp_path)
                    self.store.store_field(
                        run_date_str, run_id, fhour, product, fields, valid
                    )
                    stored += 1
            except Exception as e:
                logger.debug(f"{self.status_name} f{fhour:03d} unpack/store failed: {e}")

        logger.info(
            f"Data Collector ({self.status_name}): {run_date_str} {run_id}Z, "
            f"hours {fhour_0:03d}..{fhour_end - 1:03d}; stored {stored} field(s)."
        )
        try:
            self.store.prune_except_run(
                run_date_str, run_id, products=list(self.products.keys())
            )
        except Exception as e:
            logger.debug(f"{self.status_name} prune skipped: {e}")

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch + store a single (run_date, run_id, fhour, product) hour on demand --
        the same body as collect()'s loop iteration, but with allow_fallback=True
        unconditionally (see _resolve_download_url)."""
        base_url = self.base_url()
        unpacker = self.products[product]
        url = self._resolve_download_url(
            base_url, run_date, run_id, fhour, allow_fallback=True
        )
        if not url:
            return False
        data = download_whole(url)
        if not data:
            return False
        valid = self._valid_time(run_date, run_id, fhour)
        with with_tempfile(
            data, self.tempfile_suffix, cleanup_idx=self.cleanup_idx
        ) as tmp_path:
            fields = unpacker(tmp_path)
            self.store.store_field(run_date, run_id, fhour, product, fields, valid)
            return True


def drain_backfill(config, store, collector_classes, field_catalog_adapter) -> None:
    """Service demand-driven backfill requests flagged by the frontend (404s).

    Claims pending rows (claim_backfill_requests uses SELECT ... FOR UPDATE SKIP LOCKED),
    routes each to whichever collector_classes entry owns that product via its `products`
    dict, fetches it, and marks the row done or failed. The render task then gap-fills on
    its next pass.
    """
    claimed = field_catalog_adapter.claim_backfill_requests(limit=20)
    if not claimed:
        return
    for req in claimed:
        d, run, fhour, product = (
            req["run_date"],
            req["run_id"],
            int(req["fhour"]),
            req["product"],
        )
        d_str = d.isoformat() if hasattr(d, "isoformat") else str(d)

        # Already present (raced with the normal collect_once() cycle)?
        if store.field_exists(d_str, run, fhour, product):
            field_catalog_adapter.mark_backfill(d_str, run, fhour, product, "done")
            continue

        collector_cls = next(
            (c for c in collector_classes if product in c.products), None
        )
        if collector_cls is None:
            logger.info(f"backfill: unknown product {product}; marking failed")
            field_catalog_adapter.mark_backfill(d_str, run, fhour, product, "failed")
            continue

        collector = collector_cls(config, store)
        if not collector.base_url():
            logger.warning(
                f"backfill: no '{collector.datasource_key}' datasource configured"
            )
            field_catalog_adapter.mark_backfill(d_str, run, fhour, product, "failed")
            continue

        try:
            ok = collector.backfill_hour(d_str, run, fhour, product)
            field_catalog_adapter.mark_backfill(
                d_str, run, fhour, product, "done" if ok else "failed"
            )
            logger.info(
                f"backfill {product} {d_str} {run}Z f{fhour:03d}: "
                f"{'fetched' if ok else 'upstream missing -> failed'}"
            )
        except Exception as e:
            # Transient error: leave as failed (a later re-request resets to requested).
            logger.debug(f"backfill {product} f{fhour:03d} error: {e}")
            field_catalog_adapter.mark_backfill(d_str, run, fhour, product, "failed")
