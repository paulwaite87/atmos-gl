#!/usr/bin/env python3
import argparse
import logging
import sys
import asyncio

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

logger = logging.getLogger("worldmap.orchestrate")


class MapBuilder:
    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)

        # Execution order registry
        self.task_registry = [
            ("clouds", CloudUpdater),
            ("clouds_nasa", NasaCloudUpdater),
            ("isobars", IsobarUpdater),
            ("composite", CompositeUpdater),
            ("storms", StormUpdater),
            ("quakes", QuakeUpdater),
            ("shipping", ShippingUpdater),
            ("volcanoes", VolcanoUpdater),
            ("xplanet", XPlanetRenderer),
        ]

    async def run_pipeline(self):
        logger.info("Starting WorldMap Builder Pipeline")

        # Refresh config to obtain any changes
        self.config.load()

        for section, task_class in self.task_registry:
            if section == "composite":
                if self.config.get_section("isobars").getboolean("enabled", False):
                    task_class(self.config).run()
                continue

            settings = self.config.get_section(section)
            if settings.getboolean("enabled", fallback=False):
                try:
                    updater = task_class(self.config)
                    if section == "shipping":
                        await updater.run()
                    else:
                        updater.run()
                except Exception as e:
                    logger.error(f"Task '{section}' failed: {e}")
            else:
                logger.info(f"Task '{section}' is disabled - skipping")

        logger.info("Map Builder Pipeline Finished")


def main():
    parser = argparse.ArgumentParser(description="WorldMap Builder")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    setup_logging()
    map_builder = MapBuilder(args.config)

    try:
        asyncio.run(map_builder.run_pipeline())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
