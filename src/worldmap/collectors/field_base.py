#!/usr/bin/env python3
# Validated: ast.parse clean (2026-07-02).
"""FieldCollectorBase + CycleContext — shared scaffolding for the GFS/RTOFS forecast-field
collectors (gfs_atmos.py, gfs_waves.py, rtofs_currents.py — Phase 3, in progress).

CollectorBase's is_stale/has_new_data scheduling contract doesn't fit these sources: they
aren't polled on their own cadence, they're driven every full-refresh cycle (already gated by
data_collector.enabled + update_minutes at the service level) and self-gate per forecast hour
via fieldstore.field_exists(). What they DO need from CollectorBase is the config/db/settings
plumbing, so FieldCollectorBase stays a subclass but replaces the per-source scheduling with:

  * a fieldstore handle (self.store), bound once at construction like FieldIngest today
  * self.base_url()   — the configured datasources{} URL for this source
  * self.cache_hours  — the shared data_collector.cache_hours window
  * collect(ctx)      — takes a CycleContext, unlike CollectorBase's bare collect()

CycleContext exists because GfsAtmosCollector and GfsWavesCollector both need the SAME GFS
run baseline. Whoever drives the field collectors each full-refresh pass constructs ONE
CycleContext and passes it to every collect(ctx) call; the second collector asking for a
given baseline key gets the memoised result instead of a second NOMADS probe.
"""
import logging

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
