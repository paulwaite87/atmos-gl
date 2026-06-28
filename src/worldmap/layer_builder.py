#!/usr/bin/env python3
import argparse
import logging
import sys
import os
import signal
import asyncio
from datetime import datetime
from typing import Dict, Optional, Type, Tuple, List, Any

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

        self.starting_up = True
        self.last_run_times: Dict[str, datetime] = {}

        signal.signal(signal.SIGUSR1, self.handle_force_refresh)

        # Execution order registry: Isobars must run before Precip/Clouds to set the baseline
        self.task_registry: List[Tuple[str, Type[Any]]] = [
            # ("isobars", IsobarUpdater),
            # ("precipitation", PrecipitationUpdater),
            # ("clouds", CloudUpdater),
            # ("wind", WindUpdater),
            # ("sst", SSTUpdater),
            # ("currents", CurrentsUpdater),
            # ("waves", WavesUpdater),
            # ("temperature", TemperatureUpdater),
            # ("ozone", OzoneUpdater),
            # ("stormwatch", StormwatchUpdater),
            ("markers", MarkerUpdater),
        ]

    def refresh_settings(self):
        self.config.load()
        self.enabled = self.config.get_setting("layer_builder", "enabled")
        # Adjust log level if changed
        log_level = self.config.get_setting("common", "log_level")
        if log_level:
            set_loglevel(log_level)

    def handle_force_refresh(self, signum, frame):
        """Signal handler to reset the schedule."""
        logger.debug("External trigger received (SIGUSR1): Resetting task timings")
        self.last_run_times.clear()

    def tasks_ready_to_run(self) -> bool:
        for section, task_class in self.task_registry:
            updater = task_class(self.config, self.map_data)
            if self.should_run(updater):
                return True
        return False

    def should_run(self, updater: Updater) -> bool:
        """
        Determines if an updater task is due based on runs_per_day.
        Returns True if the elapsed time exceeds (86400 / runs_per_day).
        """
        # Refresh everything if config changed
        if self.starting_up or self.config.has_changed:
            return True

        runs_per_day = int(updater.settings.get("runs_per_day", 0))
        if runs_per_day <= 0:
            return False

        # Calculate frequency interval
        interval_seconds: float = 86400.0 / runs_per_day

        last_run: Optional[datetime] = self.last_run_times.get(updater.section, None)

        if last_run is None:
            return True

        elapsed_seconds: float = (datetime.now() - last_run).total_seconds()
        return elapsed_seconds >= interval_seconds

    async def start_scheduler(self):
        while True:
            self.refresh_settings()

            if self.enabled:
                self.map_data.refresh()

                if (
                    self.starting_up
                    or self.config.has_changed
                    or self.tasks_ready_to_run()
                ):
                    logger.info("Layer-builder scheduler run started")

                    # Re-sync the model datum each scheduling run. The GFS/RTOFS baselines
                    # are cached in shared_state for intra-run consistency (every layer in
                    # one run shares one datum), but they MUST be cleared between runs or
                    # they pin to the first run for the life of the process: the forecast
                    # hour then climbs against an ever-older run until its data is pruned,
                    # and baseline-driven paths (current-hour publish, the waves tile GRIB
                    # download) silently go stale. Clearing here forces a fresh resolve.
                    self.map_data.shared_state.pop("gfs_baseline", None)
                    self.map_data.shared_state.pop("rtofs_baseline", None)

                    for section, task_class in self.task_registry:
                        logger.debug(f"Updater task '{section}' checking runnable")
                        updater = task_class(self.config, self.map_data)
                        if self.should_run(updater):
                            try:
                                logger.info(f"Running scheduled task: '{section}'")

                                # Handle both sync and async run methods
                                if section in ["shipping", "lightning"]:
                                    await updater.run()
                                else:
                                    updater.run()

                                # Timestamp the completion with high precision
                                self.last_run_times[section] = datetime.now()

                            except Exception as e:
                                logger.error(
                                    f"Task '{section}' execution failed: {e}",
                                    exc_info=True,
                                )

                    self.starting_up = False
                    logger.info("Layer-builder scheduler run finished")
            else:
                logger.info("Layer-builder scheduler disabled: skipping")

            # Heartbeat sleep
            await asyncio.sleep(10)


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