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
from worldmap.lib.logging import setup_logging, set_loglevel
from worldmap.tasks.common import COMPOSITE_SECTIONS


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
from worldmap.tasks.composite import CompositeUpdater
from worldmap.tasks.storms import StormUpdater
from worldmap.tasks.lightning import LightningUpdater
from worldmap.tasks.quakes import QuakeUpdater
from worldmap.tasks.satellites import SatelliteUpdater
from worldmap.tasks.shipping import ShippingUpdater
from worldmap.tasks.volcanoes import VolcanoUpdater

logger = logging.getLogger("worldmap.layer_builder")


class LayerBuilder:
    enabled = False

    def __init__(self, config_path: str):
        self.config = WorldMapConfig(config_path)
        self.map_data = MapData(self.config)

        # Initialize a shared state dictionary for inter-updater communication
        self.map_data.shared_state = {}

        self.starting_up = True
        self.last_run_times: Dict[str, datetime] = {}
        self.map_updated = False
        self.composite_layers_updated = False

        signal.signal(signal.SIGUSR1, self.handle_force_refresh)

        # Execution order registry: Isobars must run before Precip/Clouds to set the baseline
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
            ("storms", StormUpdater),
            ("composite", CompositeUpdater),
            ("lightning", LightningUpdater),
            ("quakes", QuakeUpdater),
            ("satellites", SatelliteUpdater),
            ("shipping", ShippingUpdater),
            ("volcanoes", VolcanoUpdater),
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
                logger.debug(
                    f"Clearing {updater.section} output file {updater.output_path}"
                )
                updater.remove_output_file()
            return False

        # Refresh everything if config changed
        if self.starting_up or self.config.has_changed:
            return True

        # Composite produces the weather image from clouds and/or isobars,
        # so we run that if they were updated
        if updater.section == "composite" and self.composite_layers_updated:
            return True

        # Handle special case of xplanet renderer, which doesn't have a schedule
        # and updates when either the configuration or the map has changed
        if updater.section == "xplanet" and self.map_updated:
            return True

        if updater.section == "satellites":
            update_minutes = updater.settings.get("update_minutes", 10)
            runs_per_day = int((24 * 60) / update_minutes)
        else:
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
                self.map_updated = False
                self.composite_layers_updated = False

                if (
                    self.starting_up
                    or self.config.has_changed
                    or self.tasks_ready_to_run()
                ):
                    logger.info("Map-builder scheduler run started")

                    for section, task_class in self.task_registry:
                        logger.debug(f"Updater task '{section}' checking runnable")
                        updater = task_class(self.config, self.map_data)
                        if self.should_run(updater, clear_output=True):
                            try:
                                logger.info(f"Running scheduled task: '{section}'")

                                # Handle both sync and async run methods
                                if section in ["shipping", "lightning"]:
                                    await updater.run()
                                else:
                                    updater.run()

                                # Timestamp the completion with high precision
                                self.last_run_times[section] = datetime.now()

                                # Will trigger xplanet to update
                                self.map_updated = True

                                # Will allow composite overlay to update
                                if section in COMPOSITE_SECTIONS:
                                    self.composite_layers_updated = True

                            except Exception as e:
                                logger.error(f"Task '{section}' execution failed: {e}")

                    self.starting_up = False
                    logger.info("Map-builder scheduler run finished")
            else:
                logger.info("Map-builder scheduler disabled: skipping")

            # Heartbeat sleep
            await asyncio.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="WorldMap Builder Scheduler")
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
