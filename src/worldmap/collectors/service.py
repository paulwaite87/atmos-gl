#!/usr/bin/env python3
"""CollectorService — the single orchestrator for all backend data collection.

Owns scheduling and supervision only; every "what to fetch and how" detail lives in a
collaborator, so adding a source never touches this file:

  * Field ingestion + backfill  -> FieldIngest        (collectors/field_ingest.py)
  * File-cache sources (sst/clouds) -> collect_file_caches()  (collectors/__init__.py)
  * DB event feeds              -> collect_event_feeds()       (collectors/__init__.py)
  * Async collectors (shipping/lightning) -> supervised in-process asyncio tasks

Two cadences (identical semantics to the old DataCollector.run()):
  * Full refresh every update_period_s (update_minutes / legacy update_hours): the
    synchronous collectors run in a worker thread (asyncio.to_thread) so their blocking,
    GIL-releasing downloads don't starve the embedded async collectors' event loop.
  * Backfill drain every backfill_poll_seconds so frontend-flagged (404) missing hours
    fill within ~a minute rather than waiting for the next full cycle.

This replaces DataCollector; worldmap.data_collector is now a thin backward-compat shim
that calls this. The follow-on refactor decomposes FieldIngest into per-source
FieldCollectorBase classes sharing a per-cycle baseline context.

Phase 3 status: GfsAtmosCollector/GfsWavesCollector/RtofsCurrentsCollector now run in SHADOW
alongside FieldIngest.collect_cycle() each full-refresh pass (see _run_field_collectors).
FieldIngest still runs first and remains authoritative — the shadow pass sees the same
fieldstore, so in steady state field_exists() already returns True for everything and each
collect() is a fast no-op. This exercises baseline resolution and CycleContext sharing for
real without changing what's actually fetched/stored. Once the shadow pass has run cleanly
for a while, it replaces the FieldIngest.collect_cycle() call outright and field_ingest.py
is deleted.
"""
import asyncio
import logging

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import setup_logging, set_loglevel
from worldmap.lib import fieldstore
from worldmap.collectors import collect_event_feeds, collect_file_caches
from worldmap.collectors.field_ingest import FieldIngest
from worldmap.collectors.field_base import CycleContext
from worldmap.collectors.gfs_atmos import GfsAtmosCollector
from worldmap.collectors.gfs_waves import GfsWavesCollector
from worldmap.collectors.rtofs_currents import RtofsCurrentsCollector

# The Phase 3 FieldCollectorBase subclasses, run in shadow each cycle (see module
# docstring). Order doesn't matter for correctness — GfsAtmosCollector and GfsWavesCollector
# share their CycleContext("gfs") baseline regardless of which of the two runs first.
_FIELD_COLLECTOR_CLASSES = [GfsAtmosCollector, GfsWavesCollector, RtofsCurrentsCollector]

logger = logging.getLogger("worldmap.collector_service")

# Async collectors that can run IN-PROCESS instead of as their own Docker service. Keyed
# by config-section name; resolved lazily (importlib) so a missing optional dependency for
# one collector can't break service startup. Selected via config:
# data_collector.embedded_collectors = ["shipping_collector", "lightning_collector"].
_EMBEDDABLE_COLLECTORS = {
    "shipping_collector": ("worldmap.collectors.shipping", "ShippingCollector"),
    "lightning_collector": ("worldmap.collectors.lightning", "LightningCollector"),
}


def _resolve_embeddable(name):
    spec = _EMBEDDABLE_COLLECTORS.get(name)
    if spec is None:
        return None
    import importlib

    module_name, cls_name = spec
    return getattr(importlib.import_module(module_name), cls_name)


class CollectorService:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()

        # Bind the fieldstore to this process's workdir + db handle (bulk field arrays
        # live under {workdir}/fields; the db keeps only the catalog rows), then hand it
        # to FieldIngest which owns all GFS/RTOFS ingestion + backfill.
        workdir = self.config.get_setting("common", "workdir", ".")
        self.workdir = workdir
        self.store = fieldstore.get_store(workdir, db=self.db)
        self.fields = FieldIngest(self.config, self.db, self.store)

        # One last_runs dict per synchronous collector family; each collector's section is
        # the key, mutated in place so per-collector cadence counts down across cycles.
        self._event_last_runs: dict[str, float] = {}
        self._cache_last_runs: dict[str, float] = {}
        logger.debug("Initializing CollectorService")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("data_collector") or {}
        # Full-refresh cadence. Prefer update_minutes (finer control); fall back to the
        # legacy update_hours for existing configs. Stored as seconds.
        if self.settings.get("update_minutes") is not None:
            self.update_period_s = int(self.settings.get("update_minutes")) * 60
        else:
            self.update_period_s = int(self.settings.get("update_hours", 12)) * 3600
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)
        # FieldIngest reads datasources + cache_hours from the same section; keep it in
        # step so a live config edit reaches the field cycle too. (Guarded because
        # refresh_settings runs once in __init__ before self.fields exists.)
        if getattr(self, "fields", None) is not None:
            self.fields.refresh()

    # ------------------------------------------------------------------
    # Synchronous full refresh — offloaded to a thread by run()
    # ------------------------------------------------------------------
    def collect_once(self):
        """One full pass over every synchronous collector family. Each family self-gates
        on its own cadence/freshness, so a steady-state cycle is cheap."""
        # Heavy field datasources (gfs atmos+waves, rtofs currents): fieldstore-backed,
        # baseline-resolved, per-hour skip-if-present.
        self.fields.collect_cycle()

        # Shadow pass: the Phase 3 FieldCollectorBase subclasses, exercised alongside
        # FieldIngest (see module docstring). A failure here is logged but never allowed to
        # break the cycle FieldIngest already completed.
        self._run_field_collectors()

        # File-cache collectors: sst (OISST netCDF), clouds (GIBS image). Each self-gates
        # on its own cadence (is_stale) and freshness (remote_is_newer / expiry_hours).
        collect_file_caches(self.config, self.db, self._cache_last_runs)

        # Event feeds: quakes, storms, volcanoes, satellites, markers. Each runs at its
        # own schedule via is_stale(); has_new_data() skips unchanged remotes (HEAD/ETag).
        collect_event_feeds(self.config, self.db, self._event_last_runs)

    def _run_field_collectors(self):
        """Construct each Phase 3 field collector fresh this cycle (same per-cycle
        instantiation convention as _drive() uses for the event feeds, so a live config
        edit — e.g. cache_hours — reaches them without a restart) and share one
        CycleContext across all of them, so GfsAtmosCollector and GfsWavesCollector resolve
        their common GFS baseline only once."""
        ctx = CycleContext()
        for CollectorCls in _FIELD_COLLECTOR_CLASSES:
            try:
                CollectorCls(self.config, self.db, self.store).collect(ctx)
            except Exception as e:
                logger.error(
                    f"shadow field collector {CollectorCls.__name__} failed: {e}",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Embedded async collector supervision
    # ------------------------------------------------------------------
    async def _supervise_collector(self, collector_cls):
        """Run one embedded async collector in-process, restarting it on crash.

        Each embedded collector constructs its OWN Database() (psycopg2 connections aren't
        shareable across threads, and these collectors offload their DB writes via
        asyncio.to_thread), and keeps its own `enabled` kill-switch — checked inside its
        run() loop — so it can still be paused independently (e.g. to back off a rate
        limit) without touching the service. A crash is logged and the collector is
        restarted after a short delay rather than taking the whole process down.
        """
        name = collector_cls.__name__
        while True:
            try:
                collector = collector_cls(self.config_path)
                logger.info(f"Embedded collector {name}: started.")
                await collector.run()
                # run() is an infinite loop; returning means it exited cleanly somehow.
                logger.warning(f"Embedded collector {name}: run() returned; restarting.")
            except asyncio.CancelledError:
                logger.info(f"Embedded collector {name}: cancelled.")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Embedded collector {name}: crashed ({exc}); restarting in 30s.",
                    exc_info=True,
                )
            await asyncio.sleep(30)

    def _spawn_embedded_collectors(self):
        """Spawn the configured in-process async collectors as supervised tasks.

        Defaults to ALL known embeddable collectors, so that removing their standalone
        Docker services doesn't silently stop them. Override via config to run a subset
        in-process (e.g. [] to run none here and keep them as standalone services). Each
        collector's own `enabled` flag still gates whether it actually collects, so an
        embedded-but-disabled collector simply idles.
        """
        names = (self.config.get_section("data_collector") or {}).get(
            "embedded_collectors", list(_EMBEDDABLE_COLLECTORS)
        )
        tasks = []
        for name in names:
            cls = _resolve_embeddable(name)
            if cls is None:
                logger.warning(f"Unknown embedded collector '{name}'; skipping.")
                continue
            tasks.append(
                asyncio.create_task(self._supervise_collector(cls), name=f"embed:{name}")
            )
            logger.info(f"CollectorService: running '{name}' in-process.")
        return tasks

    # ------------------------------------------------------------------
    # Async run loop
    # ------------------------------------------------------------------
    async def run(self):
        # collect_once() and the backfill drain are SYNCHRONOUS and do blocking network +
        # CPU work, so they're offloaded to a worker thread (asyncio.to_thread). That keeps
        # the event loop free for the embedded async collectors (shipping/lightning), which
        # would otherwise be starved while a weather cycle runs. The heavy numeric work
        # (cfgrib decode, numpy) releases the GIL, so the embedded collectors keep ticking.
        embedded_tasks = self._spawn_embedded_collectors()

        poll_s = int(self.settings.get("backfill_poll_seconds", 60))
        last_full = None  # None => run a full refresh immediately on first iteration
        try:
            while True:
                self.refresh_settings()  # recomputes update_period_s, fields.refresh(), etc.
                poll_s = int(self.settings.get("backfill_poll_seconds", poll_s))
                full_period = self.update_period_s
                enabled = self.settings.get("enabled", False)
                now = asyncio.get_event_loop().time()

                if enabled and (last_full is None or (now - last_full) >= full_period):
                    logger.info("CollectorService: refreshing datasets")
                    try:
                        await asyncio.to_thread(self.collect_once)
                    except Exception as e:
                        logger.error(f"CollectorService cycle failed: {e}")
                    last_full = now
                elif not enabled:
                    logger.debug("CollectorService disabled. Skipping full refresh.")

                # Backfill drain runs every poll regardless of the full-refresh timer
                # (still gated on enabled, so a disabled service does nothing).
                if enabled:
                    try:
                        await asyncio.to_thread(self.fields.drain_backfill)
                    except Exception as e:
                        logger.error(f"backfill drain failed: {e}")

                await asyncio.sleep(max(5, poll_s))
        finally:
            for t in embedded_tasks:
                t.cancel()
            if embedded_tasks:
                await asyncio.gather(*embedded_tasks, return_exceptions=True)


def main():
    import argparse

    setup_logging()
    parser = argparse.ArgumentParser(description="WorldMap Collector Service")
    parser.add_argument("--config", required=True, help="Path to worldmap.json")
    args = parser.parse_args()
    try:
        asyncio.run(CollectorService(args.config).run())
    except KeyboardInterrupt:
        logger.info("CollectorService gracefully stopped.")


if __name__ == "__main__":
    main()
