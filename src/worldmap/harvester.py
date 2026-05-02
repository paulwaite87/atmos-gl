#!/usr/bin/env python3
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

        sub = {
            "APIKey": api_key,
            "BoundingBoxes": [bbox],
            "FilterMessageTypes": ["ShipStaticData", "PositionReport"],
        }

        static_count = 0
        pos_count = 0

        try:
            # ping_interval and ping_timeout help detect dead connections
            async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20
            ) as ws:
                await ws.send(json.dumps(sub))
                start_time = asyncio.get_event_loop().time()

                logger.info(f"Harvesting for {duration}s")
                logger.info(f"{label}")

                while asyncio.get_event_loop().time() - start_time < duration:
                    try:
                        # Short timeout to allow loop to check the clock
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg = json.loads(msg_raw)
                        m_type = msg.get("MessageType")
                        meta = msg.get("MetaData", {})

                        mmsi = str(meta.get("MMSI") or "")
                        if not mmsi:
                            continue

                        # --- Handle Static Data ---
                        if m_type == "ShipStaticData":
                            body = msg.get("Message", {}).get("ShipStaticData", {})
                            # Offload blocking DB call to a thread to keep loop responsive
                            await asyncio.to_thread(db.update_ship_static_data, mmsi, meta, body)
                            static_count += 1

                        # --- Handle Position Reports ---
                        elif m_type == "PositionReport":
                            body = msg.get("Message", {}).get("PositionReport", {})
                            # Offload blocking DB call to a thread
                            await asyncio.to_thread(db.update_ship_position_data, mmsi, body)
                            pos_count += 1

                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        logger.warning(f"WebSocket closed unexpectedly in {label}")
                        break
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
        sleep_between_runs = self.settings.getint("sleep_interval", fallback=60)
        track_expiry = self.settings.getint("vessel_track_expiry_days", fallback=30)

        # Calculate harvest chunks based on worldmap.conf setting
        num_chunks = self.settings.getint("harvest_chunks", fallback=12)
        slice_width = 360.0 / num_chunks

        while True:
            logger.info("Ship-harvester Service: Starting global rotation")
            start_total = db.get_current_ship_total()

            try:
                # 1. Maintenance: Keep the database lean
                db.prune_vessel_tracks(track_expiry)

                # 2. Sequential Global Sweep
                for i in range(num_chunks):
                    lon_start = -180.0 + (i * slice_width)
                    lon_end = lon_start + slice_width

                    # Final slice safety check to ensure full 180.0 coverage
                    if i == num_chunks - 1:
                        lon_end = 180.0

                    chunk_bbox = [[-90.0, lon_start], [90.0, lon_end]]
                    chunk_label = f"Slice {i + 1}/{num_chunks} ({lon_start:.1f}° -> {lon_end:.1f}°)"

                    await self.harvest_region(db, url, api_key, chunk_bbox, listen_duration, chunk_label)

                end_total = db.get_current_ship_total()
                logger.info(f"Rotation complete. Added {end_total - start_total} new vessels. Total: {end_total}")

            except Exception as e:
                logger.error(f"Unexpected error in harvester loop: {e}")
                await asyncio.sleep(30)

            logger.debug(f"Cooling down for {sleep_between_runs}s before next rotation")
            await asyncio.sleep(sleep_between_runs)


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