#!/usr/bin/env python3
"""CollectorService — the target orchestrator for the collector-per-file refactor.

WHAT THIS IS
------------
A thin, source-agnostic run loop that owns *scheduling and supervision only*. It knows
nothing source-specific: every "what to fetch and how" detail lives in a collector class
under this package. Adding a source becomes "one collector file + one registry entry" —
no new branch here.

This is the shape worldmap.data_collector.DataCollector should collapse into. It is
introduced alongside the existing DataCollector rather than replacing it in one step: the
sync file-cache (sst, clouds) and DB event feeds already run cleanly through the shared
driver and are wired here today. The heavy GFS/RTOFS *field* collectors are not yet
migrated — see the FIELD COLLECTORS seam below — so DataCollector remains the production
entry point until that slice lands. When it does, this file becomes the entry point and
DataCollector is deleted.

THREE FAMILIES, ONE LOOP
------------------------
  * Sync file caches  (CACHE_COLLECTORS) — driven by collect_file_caches()
  * Sync DB feeds     (COLLECTORS)       — driven by collect_event_feeds()
  * Field collectors  (FUTURE)           — FieldCollectorBase subclasses that need a
                                           per-cycle baseline CycleContext (resolve the
                                           GFS/RTOFS run ONCE, fan out to gfs_atmos +
                                           gfs_waves / currents). Not yet implemented.
  * Async collectors  (shipping/lightning) — persistent coroutines, supervised as tasks.

TWO CADENCES (mirrors DataCollector.run())
------------------------------------------
  * Full refresh every update_period_s (from update_minutes / legacy update_hours):
    runs the synchronous collectors in a worker thread (their downloads are blocking and
    GIL-releasing, so the event loop stays free for the async collectors).
  * Backfill drain + async supervision run continuously.
"""

import asyncio
import logging

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import setup_logging, set_loglevel
from worldmap.collectors import collect_event_feeds, collect_file_caches

logger = logging.getLogger("worldmap.collector_service")


class CollectorService:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.db = Database()

        # One last_runs dict per synchronous family; each collector's section is the key,
        # and _drive() mutates these in place so per-collector cadence counts down across
        # cycles. Separate dicts keep the families independently schedulable/observable.
        self._event_last_runs: dict[str, float] = {}
        self._cache_last_runs: dict[str, float] = {}

        self.refresh_settings()

    def refresh_settings(self) -> None:
        self.config.load()
        self.settings = self.config.get_section("data_collector") or {}
        if self.settings.get("update_minutes") is not None:
            self.update_period_s = int(self.settings["update_minutes"]) * 60
        else:
            self.update_period_s = int(self.settings.get("update_hours", 12)) * 3600
        lvl = self.settings.get("log_level")
        if lvl:
            set_loglevel(lvl)

    # ------------------------------------------------------------------
    # Synchronous full refresh — offloaded to a thread by run()
    # ------------------------------------------------------------------
    def collect_once(self) -> None:
        """One full pass over every synchronous collector. Each family self-gates on its
        own cadence/freshness, so calling this every poll is cheap in steady state."""
        collect_file_caches(self.config, self.db, self._cache_last_runs)
        collect_event_feeds(self.config, self.db, self._event_last_runs)

        # --- FIELD COLLECTORS seam --------------------------------------------------
        # When gfs_atmos / gfs_waves / rtofs_currents become FieldCollectorBase
        # subclasses, resolve the shared baselines ONCE here and fan them out via a
        # CycleContext, e.g.:
        #
        #   ctx = CycleContext(self.config, self.db, self.store)
        #   collect_field_sources(FIELD_COLLECTORS, ctx, self._field_last_runs)
        #
        # so the two GFS collectors never independently re-probe NOMADS for the run.
        # Until then, field ingestion stays in DataCollector.
        # ----------------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Async run loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        # embedded async collectors (shipping/lightning) would be spawned + supervised
        # here, identical to DataCollector._spawn_embedded_collectors(). Omitted from the
        # skeleton to keep the scheduling core legible.
        poll_s = int(self.settings.get("backfill_poll_seconds", 60))
        last_full: float | None = None

        while True:
            self.refresh_settings()
            poll_s = int(self.settings.get("backfill_poll_seconds", poll_s))
            enabled = self.settings.get("enabled", False)
            now = asyncio.get_event_loop().time()

            if enabled and (
                last_full is None or (now - last_full) >= self.update_period_s
            ):
                logger.info("CollectorService: full refresh")
                try:
                    # Blocking + GIL-releasing downloads run off the event loop so the
                    # (future) async collectors aren't starved during a weather cycle.
                    await asyncio.to_thread(self.collect_once)
                except Exception as e:
                    logger.error(f"CollectorService cycle failed: {e}", exc_info=True)
                last_full = now
            elif not enabled:
                logger.debug("CollectorService disabled; skipping full refresh.")

            await asyncio.sleep(max(5, poll_s))


def main() -> None:
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
