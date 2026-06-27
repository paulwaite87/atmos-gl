#!/usr/bin/env python3
import sys
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel

logger = logging.getLogger("worldmap.lightning_collector")


class LightningCollector:
    settings = None
    api_key = None
    url = None
    primary_region_label = None

    def __init__(self, config_path):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        logger.debug("Initializing Weather Scanner")

    def refresh_settings(self):
        self.config.load()
        self.primary_region_label = self.config.get_setting("common", "region")
        self.settings = self.config.get_section("lightning_collector")
        self.url = self.settings.get("url")
        self.api_key = self.settings.get("api_key", None)
        if not self.api_key:
            logger.error("OpenWeather API key not set")
            sys.exit(1)
        # Adjust log level if changed
        log_level = self.settings.get("log_level", None)
        if log_level:
            set_loglevel(log_level)

    def get_grid_for_bbox(self, bbox):
        """
        Generates 50km blocks for a specific bounding box.
        bbox format from DB/Config: (lon_min, lat_min, lon_max, lat_max)
        """
        lon_min, lat_min, lon_max, lat_max = bbox
        points = []
        step = 0.45  # ~50km

        lat = lat_min + (step / 2)
        while lat <= lat_max:
            lon = lon_min + (step / 2)
            while lon <= lon_max:
                points.append((lat, lon))
                lon += step
            lat += step
        return points

    async def fetch_and_store(self, session, lat, lon, start_iso, end_iso):
        params = {
            "lat": f"{lat:.4f}",
            "lon": f"{lon:.4f}",
            "radius": 50,
            "start_date": start_iso,
            "end_date": end_iso,
            "apikey": self.api_key,
        }
        try:
            async with session.get(self.url, params=params, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    strikes = data.get("lightnings", [])
                    for s in strikes:
                        self.db.update_lightning_strike(
                            strike_id=s["id"],
                            lat=s["lat"],
                            lon=s["lon"],
                            quality=s["quality"],
                            timestamp_iso=s["datetime"],
                        )
                    return len(strikes)
                elif resp.status == 429:
                    logger.warning("Rate limit hit, slowing down...")
                    await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f"Block {lat},{lon} failed: {e}")
        return 0

    async def scan_region(self, session, label, bbox, start_iso, end_iso):
        """Processes all blocks within a specific named region."""
        grid = self.get_grid_for_bbox(bbox)
        logger.debug(f"Scanning region '{label}': {len(grid)} blocks.")

        # Batch size of 5 to remain stable
        for i in range(0, len(grid), 5):
            batch = grid[i : i + 5]
            tasks = [
                self.fetch_and_store(session, p[0], p[1], start_iso, end_iso)
                for p in batch
            ]
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)

    async def run(self):
        while True:
            self.refresh_settings()

            if self.settings.get("enabled", False):
                logger.info("Weather Scanner Service: Starting regional scans.")

                # Time window for scan
                now = datetime.now(timezone.utc)
                start_iso = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
                end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Get the ordered list from the DB
                regions = self.db.get_priority_region_list(self.primary_region_label)

                async with aiohttp.ClientSession() as session:
                    for reg in regions:
                        label = reg["label"]
                        bbox_tuple = (
                            reg["lon_min"],
                            reg["lat_min"],
                            reg["lon_max"],
                            reg["lat_max"],
                        )

                        # Log if this is the priority region for visibility in logs
                        prefix = (
                            "[PRIORITY] " if label == self.primary_region_label else ""
                        )
                        logger.debug(f"{prefix}Starting scan for {label}")

                        await self.scan_region(
                            session, label, bbox_tuple, start_iso, end_iso
                        )

                # Prune old data
                expiry_hours = self.settings.get("expiry_hours", 2)
                pruned = self.db.prune_lightning(expiry_hours=expiry_hours)
                if pruned:
                    logger.debug(f"Pruned {pruned} expired strikes.")

                logger.info("Scan complete.")
            else:
                logger.debug("Weather Scanner Service: disabled. Skipping scan.")

            await asyncio.sleep(600)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    scanner = LightningCollector(args.config)
    asyncio.run(scanner.run())


if __name__ == "__main__":
    main()
