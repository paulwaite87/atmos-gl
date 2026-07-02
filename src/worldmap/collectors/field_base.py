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

from .base import CollectorBase

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
    """

    section = "data_collector"
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
