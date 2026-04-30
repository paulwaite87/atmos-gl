#!/usr/bin/env python3
import os
import sys
import json
import logging
import asyncio
import websockets
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.shipping import ShipDatabase

logger = logging.getLogger("worldmap.harvester")


class ShipHarvester:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.workdir = self.config.get_section("common").get("workdir", ".")
        logger.debug(f"Workdir: {self.workdir} - Initializing Ship Harvester")
        self.load_settings()

    def load_settings(self):
        self.config.load()
        self.settings = self.config.get_section("shipping_harvester")

    async def harvest_region(self, db, url, api_key, bbox, duration, label):
        """Connects to AIS stream and processes messages for a specific bbox."""

        # Format for AIS WebSocket API: [[LatS, LonW], [LatN, LonE]]
        sub = {
            "APIKey": api_key,
            "BoundingBoxes": [bbox],
            "FilterMessageTypes": ["ShipStaticData", "PositionReport"],
        }

        static_count = 0
        pos_count = 0

        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps(sub))
                start_time = asyncio.get_event_loop().time()

                logger.info(f"Monitoring '{label}' for {duration}s")

                while asyncio.get_event_loop().time() - start_time < duration:
                    try:
                        # Short timeout to allow loop to check the clock (start_time)
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg = json.loads(msg_raw)
                        m_type = msg.get("MessageType")
                        meta = msg.get("MetaData", {})

                        # Extract MMSI - essential for both types
                        mmsi = str(meta.get("MMSI") or "")
                        if not mmsi:
                            continue

                        # --- Handle Static Data ---
                        if m_type == "ShipStaticData":
                            body = msg.get("Message", {}).get("ShipStaticData", {})
                            db.update_ship_static_data(mmsi, meta, body)
                            static_count += 1

                        # --- Handle Position Reports ---
                        elif m_type == "PositionReport":
                            body = msg.get("Message", {}).get("PositionReport", {})
                            db.update_ship_position_data(mmsi, body)
                            pos_count += 1

                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Error processing message in {label}: {e}")

                logger.info(f"Updated {static_count} static, {pos_count} positions")

        except Exception as e:
            logger.error(f"Connection error for region {label}: {e}")

    async def run(self):
        self.load_settings()
        db = ShipDatabase()

        url = self.settings.get("url")
        api_key = self.settings.get("api_key")
        listen_duration = self.settings.getint("listen_duration", fallback=300)

        # New setting: How long to wait before starting the next harvest cycle
        sleep_between_runs = self.settings.getint("sleep_interval", fallback=60)

        # Get regions from [shipping_harvester] section
        region_labels = json.loads(self.settings.get("regions", fallback="[]"))

        while True:
            logger.info("Ship-harvester Service run started")
            start_total = db.get_current_ship_total()
            logger.info(f"{start_total} ships in the database")
            try:
                # Refresh bboxes in case the database regions changed
                bboxes_data = db.get_active_bboxes(region_labels)

                if not bboxes_data:
                    logger.error("No valid bounding boxes found. Retrying in 120s")
                    await asyncio.sleep(120)
                    continue


                for box in bboxes_data:
                    # Reformat list to the nested API requirement
                    api_bbox = [[box[0], box[1]], [box[2], box[3]]]
                    current_label = "Targeted Zone" if region_labels else "Global World"

                    await self.harvest_region(db, url, api_key, api_bbox, listen_duration, current_label)

                end_total = db.get_current_ship_total()
                logger.info(f"Added {end_total - start_total} ships. Database total: {end_total}")

            except Exception as e:
                logger.error(f"Unexpected error in harvester loop: {e}")
                await asyncio.sleep(30)  # Prevent rapid-fire crashing

            logger.debug(f"Sleeping for {sleep_between_runs}s")
            await asyncio.sleep(sleep_between_runs)

            logger.info("Ship-harvester Service run finished")


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    harvester = ShipHarvester(args.config)
    asyncio.run(harvester.run())


if __name__ == "__main__":
    main()