#!/usr/bin/env python3
import logging
import asyncio
import aiohttp

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel

logger = logging.getLogger("worldmap.satellites_collector")

CELESTRAK_GROUPS = ["stations", "weather", "science", "resource"]

class SatellitesCollector:
    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        logger.debug("Initializing Satellites Collector")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("satellites_collector")
        self.base_url = self.settings.get(
            "url", "https://celestrak.org/NORAD/elements"
        ).rstrip("/")
        self.update_hours = int(self.settings.get("update_hours", 12))
        log_level = self.settings.get("log_level")
        if log_level:
            set_loglevel(log_level)

    async def fetch_group(self, session, group):
        url = f"{self.base_url}/gp.php?GROUP={group}&FORMAT=json"
        try:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    # CelesTrak sometimes serves JSON as text/plain
                    return await resp.json(content_type=None)
                logger.warning(f"Group '{group}' returned HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Failed to fetch group '{group}': {e}")
        return []

    async def run(self):
        while True:
            self.refresh_settings()
            if self.settings.get("enabled", False):
                logger.info("Satellites Collector: refreshing orbital elements.")
                stored = 0
                async with aiohttp.ClientSession() as session:
                    for group in CELESTRAK_GROUPS:
                        records = await self.fetch_group(session, group)
                        for rec in records:
                            try:
                                norad = int(rec["NORAD_CAT_ID"])
                                name = rec.get("OBJECT_NAME", str(norad))
                                # store the FULL OMM dict verbatim — omm.initialize needs it all
                                await asyncio.to_thread(
                                    self.db.update_satellite,
                                    norad,
                                    name,
                                    rec,
                                    rec.get("EPOCH"),
                                )
                                stored += 1
                            except Exception as e:
                                logger.debug(f"Skipping malformed record: {e}")
                logger.info(
                    f"Satellite element update complete. Stored/updated {stored} objects."
                )
            else:
                logger.debug("Satellites Collector disabled. Skipping.")
            await asyncio.sleep(self.update_hours * 3600)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(SatellitesCollector(args.config).run())


if __name__ == "__main__":
    main()
