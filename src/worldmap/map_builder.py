#!/usr/bin/env python3
import argparse
import logging
import sys
import asyncio
from datetime import datetime
from typing import Dict, Optional, Type, Tuple, List, Any

# Library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.logging import setup_logging

# Task imports
from worldmap.tasks.clouds import CloudUpdater
from worldmap.tasks.clouds_nasa import NasaCloudUpdater
from worldmap.tasks.isobars import IsobarUpdater
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

        # Explicitly typed dictionary for tracking task completion
        self.last_run_times: Dict[str, datetime] = {}

        # Flag to keep track of whether updates happened in the loop
        # This gets reset every time a loop starts
        self.map_updated = False

        # Execution order registry
        self.task_registry: List[Tuple[str, Type[Any]]] = [
            ("clouds", CloudUpdater),
            ("clouds_nasa", NasaCloudUpdater),
            ("isobars", IsobarUpdater),
            ("composite", CompositeUpdater),
            ("storms", StormUpdater),
            ("quakes", QuakeUpdater),
            ("shipping", ShippingUpdater),
            ("volcanoes", VolcanoUpdater),
            ("xplanet", XPlanetRenderer),  # always keep renderer last
        ]

    def some_tasks_ready_to_run(self) -> bool:
        for section, _ in self.task_registry:
            if section == "composite":
                continue
            if self.should_run(section):
                return True
        return False

    def should_run(self, section: str) -> bool:
        """
        Determines if a task is due based on runs_per_day.
        Returns True if the elapsed time exceeds (86400 / runs_per_day).
        """
        # This section depends on isobars; always run
        if section == "composite":
            return True

        settings = self.config.get_section(section)

        if not settings.getboolean("enabled", fallback=False):
            return False

        # Handle special case of xplanet renderer, which doesn't
        # have a schedule and updates when the map has changed
        if section == "xplanet" and self.map_updated:
            return True

        try:
            runs_per_day: int = settings.getint("runs_per_day", fallback=0)
        except ValueError:
            logger.error(f"Invalid 'runs_per_day' in section [{section}]. Expected integer.")
            return False

        if runs_per_day <= 0:
            return False

        # Calculate frequency interval
        interval_seconds: float = 86400.0 / runs_per_day

        last_run: Optional[datetime] = self.last_run_times.get(section, None)

        if last_run is None:
            return True

        elapsed_seconds: float = (datetime.now() - last_run).total_seconds()
        return elapsed_seconds >= interval_seconds

    async def start_scheduler(self):

        while True:
            self.config.load()
            self.map_updated = False

            if self.some_tasks_ready_to_run():

                logger.info("Map-builder scheduler run started")

                for section, task_class in self.task_registry:
                    # Composite logic depends on isobar settings
                    if section == "composite":
                        if not self.config.get_section("isobars").getboolean("enabled", False):
                            continue

                    if self.should_run(section):
                        try:
                            logger.info(f"Running scheduled task: '{section}'")
                            updater = task_class(self.config)

                            # Handle both sync and async run methods
                            if section == "shipping":
                                await updater.run()
                            else:
                                updater.run()

                            # Timestamp the completion with high precision
                            self.last_run_times[section] = datetime.now()

                            self.map_updated = True

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