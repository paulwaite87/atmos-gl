#!/usr/bin/env python3
import argparse
import logging
import sys
import os
import signal
import asyncio
from typing import Type, Tuple, List, Any

# Library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.logging import setup_logging, set_loglevel


# Task imports
from worldmap.tasks.common import MapData, Updater
from worldmap.tasks.clouds import CloudUpdater
from worldmap.tasks.isobars import IsobarUpdater
from worldmap.tasks.wind import WindUpdater
from worldmap.tasks.precipitation import PrecipitationUpdater
from worldmap.tasks.sst import SSTUpdater
from worldmap.tasks.currents import CurrentsUpdater
from worldmap.tasks.waves import WavesUpdater
from worldmap.tasks.temperature import TemperatureUpdater
from worldmap.tasks.ozone import OzoneUpdater
from worldmap.tasks.stormwatch import StormwatchUpdater
from worldmap.tasks.markers import MarkerUpdater

logger = logging.getLogger("worldmap.layer_builder")

# How long to wait between fan-out cycles. Every cycle runs all updaters concurrently;
# per-hour freshness checks make a steady-state (nothing-changed) cycle cheap, so this is
# just the responsiveness window for picking up new data or deleted output.
CYCLE_SECONDS = 15


class LayerBuilder:
    enabled = False

    def __init__(self, config_path: str):
        self.config = WorldMapConfig(config_path)
        self.map_data = MapData(self.config)

        # Ensure this folder exists
        data_dir = os.path.join(
            self.config.get_setting("common", "workdir", "."), "data"
        )
        os.makedirs(data_dir, exist_ok=True)

        # Initialize a shared state dictionary for inter-updater communication
        self.map_data.shared_state = {}

        signal.signal(signal.SIGUSR1, self.handle_force_refresh)

        # All layer updaters. They run CONCURRENTLY each cycle (see start_scheduler), so
        # there is no execution-order constraint any more — the only shared datum (the
        # GFS/RTOFS baseline) is resolved once, up front, before the fan-out.
        self.task_registry: List[Tuple[str, Type[Any]]] = [
            ("isobars", IsobarUpdater),
            ("precipitation", PrecipitationUpdater),
            ("clouds", CloudUpdater),
            ("wind", WindUpdater),
            ("sst", SSTUpdater),
            ("currents", CurrentsUpdater),
            ("waves", WavesUpdater),
            ("temperature", TemperatureUpdater),
            ("ozone", OzoneUpdater),
            ("stormwatch", StormwatchUpdater),
            ("markers", MarkerUpdater),
        ]

        # Persistent updater instances: built once and reused across cycles (rebuilt on
        # config change). Each owns its own DB connection, so the concurrent fan-out never
        # shares a psycopg2 connection across threads.
        self.updaters: List[Tuple[str, Updater]] = []

    def refresh_settings(self):
        self.config.load()
        self.enabled = self.config.get_setting("layer_builder", "enabled")
        # Adjust log level if changed
        log_level = self.config.get_setting("common", "log_level")
        if log_level:
            set_loglevel(log_level)

    def handle_force_refresh(self, signum, frame):
        """SIGUSR1: drop the cached GFS/RTOFS datum so the next cycle re-resolves it."""
        logger.debug("External trigger (SIGUSR1): clearing cached baselines")
        ss = getattr(self.map_data, "shared_state", None)
        if isinstance(ss, dict):
            ss.pop("gfs_baseline", None)
            ss.pop("rtofs_baseline", None)

    def _build_updaters(self):
        """(Re)instantiate every updater once. Instances are persistent and reused across
        cycles; each owns its own DB connection. Rebuilt on config change because an
        updater caches its settings/derived values at construction and config.load()
        replaces the underlying dict."""
        self.updaters = [
            (section, cls(self.config, self.map_data))
            for section, cls in self.task_registry
        ]
        logger.info(f"Built {len(self.updaters)} updater instance(s)")

    def _resolve_baselines(self):
        """Resolve the GFS/RTOFS datums ONCE, up front, into shared_state, so the
        concurrent updaters all read one cached baseline instead of racing to establish
        it. Cleared first so a long-lived process can't pin to an ever-older run."""
        ss = self.map_data.shared_state
        ss.pop("gfs_baseline", None)
        ss.pop("rtofs_baseline", None)
        if not self.updaters:
            return
        primer = self.updaters[0][1]
        for label, resolve in (
            ("GFS", primer.get_gfs_state),
            ("RTOFS", primer.get_rtofs_state),
        ):
            try:
                resolve()
            except Exception as e:
                logger.warning(f"{label} baseline pre-resolve failed: {e}")

    def _run_one(self, section: str, updater: Updater):
        """Run a single updater, isolating failures so one bad layer can't abort the
        whole cycle. Executed in a worker thread via asyncio.to_thread."""
        try:
            updater.run()
        except Exception as e:
            logger.error(f"Task '{section}' execution failed: {e}", exc_info=True)

    async def start_scheduler(self):
        # Build persistent instances once, after an initial refresh so the region/config
        # are current before any updater is constructed.
        self.refresh_settings()
        self.map_data.refresh()
        self._build_updaters()

        while True:
            self.refresh_settings()

            if self.enabled:
                self.map_data.refresh()

                # Config edits change cached settings/derived values, so rebuild instances
                # (and their connections) when the file changes.
                if self.config.has_changed:
                    logger.info("Config changed: rebuilding updaters")
                    self._build_updaters()

                # Resolve the shared datum once, then fan out: every updater runs
                # concurrently in its own thread (numpy/PIL release the GIL). No should_run
                # gating — each updater's per-hour freshness check skips work that's already
                # current, so a steady-state cycle is cheap and a changed or deleted layer
                # re-renders promptly, now-hour first.
                self._resolve_baselines()
                await asyncio.gather(
                    *(
                        asyncio.to_thread(self._run_one, section, updater)
                        for section, updater in self.updaters
                    )
                )
            else:
                logger.info("Layer-builder scheduler disabled: skipping")

            await asyncio.sleep(CYCLE_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="WorldMap Layer Builder Scheduler")
    parser.add_argument("--config", required=True, help="Path to worldmap.json")
    args = parser.parse_args()

    setup_logging()
    layer_builder = LayerBuilder(args.config)

    try:
        asyncio.run(layer_builder.start_scheduler())
    except KeyboardInterrupt:
        logger.info("Scheduler gracefully stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()