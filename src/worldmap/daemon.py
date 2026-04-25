#!/usr/bin/env python3
import argparse
import logging
import sys
import asyncio
import signal
from datetime import datetime

# Library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.logging import setup_logging
from worldmap.map_builder import MapBuilder
from worldmap.tasks.harvester import ShipHarvester

logger = logging.getLogger("worldmap.daemon")


class WorldMapDaemon:
    config = None
    settings = None
    harvester_settings = None

    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.load_settings()

        # Core components
        self.map_builder = MapBuilder(config.config_path)
        self.harvester = ShipHarvester(config.config_path)

        # Shutdown state
        self.exit_event = asyncio.Event()

    def load_settings(self):
        self.config.load()
        self.settings = self.config.get_section("daemon")
        self.harvester_settings = self.config.get_section("shipping_harvester")

    def is_morning_shift(self):
        """Replicates original logic using HH:MM string comparison."""
        now = datetime.now().strftime("%H:%M")
        morning = self.settings.get("morning", fallback="09:00")
        evening = self.settings.get("evening", fallback="23:00")
        return morning <= now < evening

    def handle_exit(self, sig):
        logger.info(f"Signal {sig.name} detected. Shutting down WorldMap daemon...")
        self.exit_event.set()

    async def run(self):
        logger.info("WorldMap System Daemon Started")

        # Signal handling for Docker
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.handle_exit, sig)

        while not self.exit_event.is_set():
            # Refresh settings
            self.load_settings()

            # Determine mode and interval at the start of the loop
            if self.is_morning_shift():
                mode_label = "MAP BUILDER"
                interval = self.settings.getint("update_sleep", fallback=120)
                try:
                    logger.info(f"[{mode_label}] Starting map update pipeline")
                    await self.map_builder.run_pipeline()
                except Exception as e:
                    logger.error(f"[{mode_label}] Error: {e}")
            else:
                mode_label = "SHIPPING HARVESTER"
                interval = self.settings.getint("harvest_sleep", fallback=600)

                if self.harvester_settings.getboolean("enabled", fallback=False):
                    try:
                        logger.info(f"[{mode_label}] Starting harvest")
                        await self.harvester.run()
                    except Exception as e:
                        logger.error(f"[{mode_label}] Error: {e}")
                else:
                    logger.info(f"[{mode_label}] Harvester is disabled - skipping")

            # We sleep for the full interval after the task finishes.
            if not self.exit_event.is_set():
                logger.info(f"Sleeping for {interval}s...")
                try:
                    # This waits for the full 'interval' UNLESS an exit signal occurs
                    await asyncio.wait_for(self.exit_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    # Normal behavior: the sleep timer expired
                    pass

        logger.info("WorldMap Daemon stopped cleanly")


def main():
    parser = argparse.ArgumentParser(description="WorldMap System Daemon")
    parser.add_argument("--config", required=True, help="Path to main config")
    args = parser.parse_args()

    setup_logging()

    try:
        config = WorldMapConfig(args.config)
        daemon = WorldMapDaemon(config)
        asyncio.run(daemon.run())
    except Exception as e:
        logger.critical(f"Daemon crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
