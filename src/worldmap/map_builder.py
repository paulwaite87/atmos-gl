#!/usr/bin/env python3
import argparse
import logging
import sys
import signal
import asyncio
from datetime import datetime
from typing import Dict, Optional, Type, Tuple, List, Any

# Library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.logging import setup_logging

# Task imports
from worldmap.tasks.common import MapData, Updater
from worldmap.tasks.clouds import CloudUpdater
from worldmap.tasks.clouds_nasa import NasaCloudUpdater
from worldmap.tasks.isobars import IsobarUpdater
from worldmap.tasks.wind import WindUpdater
from worldmap.tasks.composite import CompositeUpdater
from worldmap.tasks.storms import StormUpdater
from worldmap.tasks.quakes import QuakeUpdater
from worldmap.tasks.shipping import ShippingUpdater
from worldmap.tasks.volcanoes import VolcanoUpdater
from worldmap.tasks.renderer import XPlanetRenderer

logger = logging.getLogger("worldmap.map_builder")


class MapBuilder:
    def __init__(self, config_path: str):
        self.config = WorldMapConfig(config_path)
        self.map_data = MapData(self.config)
        self.config_changed = False

        # Explicitly typed dictionary for tracking task completion
        self.last_run_times: Dict[str, datetime] = {}

        # Flag to keep track of whether updates happened in the loop
        # This gets reset every time a loop starts
        self.map_updated = False

        # Flag to indicate isobars or clouds were updated so run composite
        self.weather_image_updated = False

        # Register the signal handler for SIGUSR1
        # In Docker/Linux, this is often signal 10
        signal.signal(signal.SIGUSR1, self.handle_force_refresh)

        # Execution order registry
        self.task_registry: List[Tuple[str, Type[Any]]] = [
            ("clouds", CloudUpdater),
            ("clouds_nasa", NasaCloudUpdater),
            ("isobars", IsobarUpdater),
            ("wind", WindUpdater),
            ("composite", CompositeUpdater),
            ("storms", StormUpdater),
            ("quakes", QuakeUpdater),
            ("shipping", ShippingUpdater),
            ("volcanoes", VolcanoUpdater),
            ("xplanet", XPlanetRenderer),  # always keep renderer last
        ]

    def handle_force_refresh(self, signum, frame):
        """Signal handler to reset the schedule."""
        logger.debug("External trigger received (SIGUSR1): Resetting task timings")
        self.last_run_times.clear()

    def tasks_ready_to_run(self) -> bool:
        for section, task_class in self.task_registry:
            updater = task_class(self.config, self.map_data)
            if section == "composite":
                continue
            if self.should_run(updater):
                return True
        return False

    def should_run(self, updater: Updater, clear_output=False) -> bool:
        """
        Determines if an updater task is due based on runs_per_day.
        Returns True if the elapsed time exceeds (86400 / runs_per_day).
        """
        # If the updater is disabled, make it remove any output, then skip
        if not updater.enabled:
            if clear_output:
                updater.remove_output_file()
            return False

        # Refresh everything if config changed
        if self.config_changed:
            return True

        # Composite produces the weather image from clouds and/or isobars,
        # so we run that if they were updated
        if updater.section == "composite" and self.weather_image_updated:
            return True

        # Handle special case of xplanet renderer, which doesn't have a schedule
        # and updates when either the configuration or the map has changed
        if updater.section == "xplanet" and self.map_updated:
            return True

        try:
            runs_per_day: int = updater.settings.getint("runs_per_day", fallback=0)
        except ValueError:
            logger.error(f"Invalid 'runs_per_day' in section [{updater.section}]. Expected integer.")
            return False

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
            self.config.load()
            self.map_data.refresh()
            self.map_updated = False
            self.weather_image_updated = False
            self.config_changed = self.config.is_changed()

            if self.config_changed or self.tasks_ready_to_run():
                logger.info("Map-builder scheduler run started")

                for section, task_class in self.task_registry:
                    logger.debug(f"Updater task '{section}' checking runnable")
                    updater = task_class(self.config, self.map_data)
                    if self.should_run(updater, clear_output=True):
                        try:
                            logger.info(f"Running scheduled task: '{section}'")

                            # Handle both sync and async run methods
                            if section == "shipping":
                                await updater.run()
                            else:
                                updater.run()

                            # Timestamp the completion with high precision
                            self.last_run_times[section] = datetime.now()

                            # Will trigger xplanet to update
                            self.map_updated = True

                            # Will allow composite overlay to update
                            if section in ["wind", "isobars", "clouds", "clouds_nasa"]:
                                self.weather_image_updated = True

                        except Exception as e:
                            logger.error(f"Task '{section}' execution failed: {e}")

                logger.info("Map-builder scheduler run finished")

            # Heartbeat sleep
            await asyncio.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="WorldMap Builder Scheduler")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    setup_logging()
    map_builder = MapBuilder(args.config)

    try:
        asyncio.run(map_builder.start_scheduler())
    except KeyboardInterrupt:
        logger.info("Scheduler gracefully stopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()