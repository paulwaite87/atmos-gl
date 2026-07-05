#!/usr/bin/env python3
"""OpenWeather lightning strike data -> database.

Long-running async collector: scans a priority-ordered list of regions every 600s,
batching 50km grid-point requests via aiohttp. Runs as its own Docker service because
the blocking GFS downloads in DataCollector.collect_once() would starve its event loop
(follow-on: asyncio.to_thread consolidation into data_collector).

Moved from src/worldmap/lightning_collector.py to src/worldmap/collectors/lightning.py
to live under the shared collectors umbrella. Core logic is unchanged.
"""
import os
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

from .base import AsyncCollectorBase

logger = logging.getLogger(__name__)


class LightningCollector(AsyncCollectorBase):
    section = "lightning_collector"
    # The scan loop sleeps a fixed 600s between passes (see run()); no setting exists for
    # the scan itself, so this is a generous fixed allowance rather than a computed value.
    heartbeat_period_s = 900.0

    def refresh_settings(self) -> None:
        super().refresh_settings()
        self.primary_region_label = self.config.get_setting("common", "region")
        self.url = self.settings.get("url")
        # API key: config file first, then environment variable.
        self.api_key = (
            self.settings.get("api_key")
            or os.environ.get("OPENWEATHER_API_KEY")
        )
        if not self.api_key:
            logger.error(
                "LightningCollector: no API key found in config or OPENWEATHER_API_KEY env var."
            )

    def get_grid_for_bbox(self, bbox):
        """Generate ~50km grid points for a bounding box (lon_min, lat_min, lon_max, lat_max)."""
        lon_min, lat_min, lon_max, lat_max = bbox
        step = 0.45  # ~50km
        points = []
        lat = lat_min + step / 2
        while lat <= lat_max:
            lon = lon_min + step / 2
            while lon <= lon_max:
                points.append((lat, lon))
                lon += step
            lat += step
        return points

    async def fetch_and_store(self, session, lat, lon, start_iso, end_iso):
        if not self.api_key:
            return 0
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
                    logger.warning("LightningCollector: rate limit hit; pausing.")
                    await asyncio.sleep(1)
        except Exception as exc:
            logger.debug(f"LightningCollector: block {lat},{lon} failed: {exc}")
        return 0

    async def scan_region(self, session, label, bbox, start_iso, end_iso):
        """Scan all 50km blocks within a region, in batches of 5."""
        grid = self.get_grid_for_bbox(bbox)
        logger.debug(f"LightningCollector: scanning '{label}': {len(grid)} blocks.")
        for i in range(0, len(grid), 5):
            batch = grid[i: i + 5]
            await asyncio.gather(
                *[self.fetch_and_store(session, p[0], p[1], start_iso, end_iso) for p in batch]
            )
            await asyncio.sleep(0.1)

    async def run(self) -> None:
        # Startup heartbeat: the Data Status UI should show "the collector is alive" the
        # moment this task starts, not leave a blank "never" until the first full scan
        # (region list * grid) completes. Percent decays from here if the first scan
        # itself hangs, so this can't mask a real problem.
        self.process_status_adapter.record_process_run(self.section, "collector", success=True)

        while True:
            self.refresh_settings()

            if self.enabled:
                logger.info("LightningCollector: starting regional scans.")
                now = datetime.now(timezone.utc)
                start_iso = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
                end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                try:
                    regions = self.db.get_priority_region_list(self.primary_region_label)
                    async with aiohttp.ClientSession() as session:
                        for reg in regions:
                            label = reg["label"]
                            bbox = (reg["lon_min"], reg["lat_min"], reg["lon_max"], reg["lat_max"])
                            prefix = "[PRIORITY] " if label == self.primary_region_label else ""
                            logger.debug(f"{prefix}Scanning {label}")
                            await self.scan_region(session, label, bbox, start_iso, end_iso)

                    expiry_hours = self.settings.get("expiry_hours", 2)
                    pruned = self.db.prune_lightning(expiry_hours=expiry_hours)
                    if pruned:
                        logger.debug(f"LightningCollector: pruned {pruned} expired strikes.")
                    logger.info("LightningCollector: scan complete.")
                    self.process_status_adapter.record_process_run(self.section, "collector", success=True)
                except Exception as exc:
                    logger.error(f"LightningCollector: scan error: {exc}")
                    self.process_status_adapter.record_process_run(
                        self.section, "collector", success=False, error=str(exc)
                    )
            else:
                logger.debug("LightningCollector: disabled.")

            await asyncio.sleep(600)


if __name__ == "__main__":
    LightningCollector.main()

def main():
    LightningCollector.main()

