#!/usr/bin/env python3
import logging
import asyncio

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel

logger = logging.getLogger("worldmap.gfs_collector")


class GFSCollector:
    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        logger.debug("Initializing GFS Collector")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("gfs_collector")
        self.base_url = self.settings.get("url").rstrip("/")
        self.update_hours = int(self.settings.get("update_hours", 12))
        self.cache_hours = int(self.settings.get("cache_hours", 24))
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)

    async def run(self):
        while True:
            self.refresh_settings()
            if self.settings.get("enabled", False):
                logger.info("GFS Collector: refreshing GFS dataset")
                # TODO: Code to be written here
                pass
            else:
                logger.debug("GFS Collector disabled. Skipping.")
            await asyncio.sleep(self.update_hours * 3600)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(GFSCollector(args.config).run())


if __name__ == "__main__":
    main()
