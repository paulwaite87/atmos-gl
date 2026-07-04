#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""FieldCollectorBase + CycleContext — shared scaffolding for the GFS/RTOFS forecast-field
collectors (gfs_atmos.py, gfs_waves.py, rtofs_currents.py — Phase 3, complete).

CollectorBase's is_stale/has_new_data scheduling contract doesn't fit these sources: they
aren't polled on their own cadence, they're driven every full-refresh cycle (already gated by
data_collector.enabled + update_minutes at the service level) and self-gate per forecast hour
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
import logging
from datetime import datetime, timedelta, timezone

from .base import CollectorBase, _estimate_next_update

logger = logging.getLogger(__name__)


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

    def __init__(self, config, db, store):
        super().__init__(config, db)
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
        """How often CollectorService._collect_fields() runs (data_collector.update_minutes,
        falling back to legacy update_hours) — mirrors CollectorService.refresh_settings's
        own fallback so data_status()'s next_update matches the real cadence."""
        if self.settings.get("update_minutes") is not None:
            return float(self.settings.get("update_minutes")) * 60
        return float(self.settings.get("update_hours", 12)) * 3600

    def data_status(self) -> dict:
        """Coverage-based override of CollectorBase.data_status(): percent is the fraction
        of this collector's expected (product x forecast-hour) cells actually present for
        the freshest run already in field_catalog — deliberately NOT a live NOMADS/RTOFS
        re-probe (data_status() must be cheap and side-effect-free; a status check
        shouldn't itself hit rate limits). last_updated/detail still come from
        process_status (written by CollectorService._collect_fields()), same as the
        CollectorBase default."""
        row = self.process_status_adapter.get_process_status(self.status_name)
        last_updated = row["last_updated"] if row else None
        last_error = row["last_error"] if row else None

        products = list(self.products.keys())
        avail = self.store.db.get_latest_run_hours(products=products) if products else None
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
        return {
            "name": self.status_name,
            "kind": "collector",
            "percent": round(percent, 1),
            "last_updated": last_updated,
            "next_update": _estimate_next_update(last_updated, period_s, self.enabled),
            "enabled": self.enabled,
            "detail": detail,
        }

    @staticmethod
    def _valid_time(run_date: str, run_id: str, fhour) -> datetime:
        run_ts = datetime.strptime(f"{run_date} {run_id}", "%Y-%m-%d %H").replace(
            tzinfo=timezone.utc
        )
        return run_ts + timedelta(hours=int(fhour))

    def backfill_hour(self, run_date: str, run_id: str, fhour: int, product: str) -> bool:
        """Fetch + store a single (run_date, run_id, fhour, product) hour on demand, for the
        frontend-flagged backfill queue (drain_backfill(), below). Returns True if the field
        was fetched and stored, False if upstream doesn't have it. Override per source."""
        raise NotImplementedError(
            f"{type(self).__name__}.backfill_hour() not implemented"
        )


def drain_backfill(config, db, store, collector_classes) -> None:
    """Service demand-driven backfill requests flagged by the frontend (404s).

    Claims pending rows (claim_backfill_requests uses SELECT ... FOR UPDATE SKIP LOCKED),
    routes each to whichever collector_classes entry owns that product via its `products`
    dict, fetches it, and marks the row done or failed. The render task then gap-fills on
    its next pass.
    """
    claimed = db.claim_backfill_requests(limit=20)
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
            db.mark_backfill(d_str, run, fhour, product, "done")
            continue

        collector_cls = next(
            (c for c in collector_classes if product in c.products), None
        )
        if collector_cls is None:
            logger.info(f"backfill: unknown product {product}; marking failed")
            db.mark_backfill(d_str, run, fhour, product, "failed")
            continue

        collector = collector_cls(config, db, store)
        if not collector.base_url():
            logger.warning(
                f"backfill: no '{collector.datasource_key}' datasource configured"
            )
            db.mark_backfill(d_str, run, fhour, product, "failed")
            continue

        try:
            ok = collector.backfill_hour(d_str, run, fhour, product)
            db.mark_backfill(d_str, run, fhour, product, "done" if ok else "failed")
            logger.info(
                f"backfill {product} {d_str} {run}Z f{fhour:03d}: "
                f"{'fetched' if ok else 'upstream missing -> failed'}"
            )
        except Exception as e:
            # Transient error: leave as failed (a later re-request resets to requested).
            logger.debug(f"backfill {product} f{fhour:03d} error: {e}")
            db.mark_backfill(d_str, run, fhour, product, "failed")
