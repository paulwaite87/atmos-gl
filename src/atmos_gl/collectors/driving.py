#!/usr/bin/env python3
"""Shared control flow for driving a family of collectors: gate on
data_collector.channel_enabled, run one collector, record success/failure
(architecture review candidate "collapse the two collector-family drivers into one
control flow").

What happens PER collector -- construction arity, whether there's an external
freshness pre-check, what per-cycle state gets threaded through -- is a real, not
incidental, difference between the synchronous event-feed/file-cache families
(EventFeedDriver: is_stale()/has_new_data() three-way branch, own last_runs timestamp
bookkeeping) and the fieldstore-backed field collectors (FieldCollectorDriver:
unconditional collect(ctx), freshness handled internally at per-forecast-hour
granularity, a shared CycleContext instead of last_runs). That's why _drive_one() stays
one full override per family rather than being hook-split further -- see this repo's
"Deepening Template-Method Hierarchies" convention and docs/adr/
0001-dont-unify-gfs-rtofs-baseline-probing.md for the same reasoning applied to a
different pair in this package.

Validated with ast.parse.
"""
import time
import logging

from atmos_gl.db.process_status_adapter import ProcessStatusAdapter

logger = logging.getLogger(__name__)


class CollectorDriver:
    def __init__(self, config, process_status_adapter=None):
        self.config = config
        self.process_status_adapter = process_status_adapter or ProcessStatusAdapter()

    def drive(self, collectors) -> None:
        channel_enabled = self.config.get_setting("data_collector", "channel_enabled", {}) or {}
        for CollectorCls in collectors:
            gate_key = self._gate_key(CollectorCls)
            if gate_key and not channel_enabled.get(gate_key, True):
                logger.debug(
                    f"{self._status_key(CollectorCls)}: channel '{gate_key}' disabled; skipping."
                )
                continue
            try:
                self._drive_one(CollectorCls)
            except Exception as exc:
                logger.error(
                    f"collector {CollectorCls.__name__} failed: {exc}", exc_info=True
                )
                self.process_status_adapter.record_process_run(
                    self._status_key(CollectorCls), "collector", success=False, error=str(exc)
                )

    def _gate_key(self, CollectorCls):
        raise NotImplementedError

    def _status_key(self, CollectorCls):
        raise NotImplementedError

    def _drive_one(self, CollectorCls):
        raise NotImplementedError


class EventFeedDriver(CollectorDriver):
    """Drives COLLECTORS/CACHE_COLLECTORS -- same shape, so this one class serves both
    collect_event_feeds() and collect_file_caches(), differing only in which tuple and
    which last_runs dict get passed in."""

    def __init__(self, config, last_runs: dict, process_status_adapter=None):
        super().__init__(config, process_status_adapter)
        self.last_runs = last_runs

    def _gate_key(self, CollectorCls):
        return CollectorCls.channel_key

    def _status_key(self, CollectorCls):
        return CollectorCls.section

    def _drive_one(self, CollectorCls):
        key = CollectorCls.section
        feed = CollectorCls(self.config)
        now = time.monotonic()
        if not feed.is_stale(self.last_runs.get(key)):
            logger.debug(
                f"{key}: not yet due "
                f"(period {feed.period_s:.0f}s, "
                f"next in {feed.period_s - (time.monotonic() - (self.last_runs.get(key) or 0)):.0f}s)."
            )
            return
        if not feed.has_new_data():
            self.last_runs[key] = now
            self.process_status_adapter.record_process_run(key, "collector", success=True)
            return
        logger.info(f"{key}: collecting...")
        self.process_status_adapter.record_process_start(key, "collector")
        feed.collect()
        self.last_runs[key] = now
        self.process_status_adapter.record_process_run(key, "collector", success=True)


class FieldCollectorDriver(CollectorDriver):
    """Drives FIELD_COLLECTOR_CLASSES -- unconditional collect(ctx) every cycle
    (FieldCollectorBase.collect() dedups internally at per-forecast-hour granularity,
    a different freshness philosophy from EventFeedDriver's is_stale/has_new_data), a
    shared CycleContext instead of last_runs. Also fires record_process_start() before
    collect() -- field collectors previously never showed "running" in the Data Status
    UI mid-fetch, unlike event feeds; this closes that gap."""

    def __init__(self, config, store, ctx, process_status_adapter=None):
        super().__init__(config, process_status_adapter)
        self.store = store
        self.ctx = ctx

    def _gate_key(self, CollectorCls):
        return CollectorCls.status_name

    def _status_key(self, CollectorCls):
        return CollectorCls.status_name

    def _drive_one(self, CollectorCls):
        key = CollectorCls.status_name
        self.process_status_adapter.record_process_start(key, "collector")
        CollectorCls(self.config, self.store).collect(self.ctx)
        self.process_status_adapter.record_process_run(key, "collector", success=True)
