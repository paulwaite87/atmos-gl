#!/usr/bin/env python3
import sys
import json
import logging
import asyncio
import websockets

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib.logging import set_loglevel

logger = logging.getLogger("worldmap.shipping_collector")

# A 10-element shipping density map (Weight x Base Duration)
# 1.0 is standard, >1.0 spends extra time, <1.0 is a quick pass
SLICE_DENSITY_MAP = {
    0: {"label": "Mid-Pacific (East)", "weight": 0.3},  # -180 to -144
    1: {"label": "Eastern Pacific / Americas West", "weight": 0.5},  # -144 to -108
    2: {"label": "Americas East / Panama / Caribbean", "weight": 1.5},  # -108 to -72
    3: {"label": "Western Atlantic", "weight": 0.8},  # -72 to -36
    4: {"label": "Eastern Atlantic / Gibraltar", "weight": 1.5},  # -36 to 0
    5: {"label": "Europe / West Africa / Mediterranean", "weight": 2.0},  # 0 to 36
    6: {"label": "Middle East / Suez / Hormuz / Aden", "weight": 2.0},  # 36 to 72
    7: {"label": "Indian Ocean / Bay of Bengal", "weight": 1.0},  # 72 to 108
    8: {"label": "SE Asia / Malacca / South China Sea", "weight": 2.0},  # 108 to 144
    9: {"label": "Australia / NZ / Japan / West Pacific", "weight": 0.9},  # 144 to 180
}


class ShippingCollector:
    settings = None
    api_key = None
    url = None

    def __init__(self, config_path):
        self.config_path = config_path
        self.config = WorldMapConfig(config_path)
        self.db = Database()
        self.refresh_settings()
        logger.debug("Initializing Shipping Collector")

        logger.info("One-time init: populating vessel_class field")

    def refresh_settings(self):
        self.config.load()
        self.settings = self.config.get_section("shipping_collector")
        self.url = self.settings.get("url")
        self.api_key = self.settings.get("api_key", None)
        if not self.api_key:
            logger.error("AIS API key not set")
            sys.exit(1)
        # Adjust log level if changed
        log_level = self.settings.get("log_level", None)
        if log_level:
            set_loglevel(log_level)

    async def collect_ships_in_region(self, bbox, duration, label):
        """Connects to AIS stream and processes messages for a specific bbox."""

        sub = {
            "APIKey": self.api_key,
            "BoundingBoxes": [bbox],
            "FilterMessageTypes": ["ShipStaticData", "PositionReport"],
        }

        static_count = 0
        pos_count = 0

        try:
            # ping_interval and ping_timeout help detect dead connections
            async with websockets.connect(
                self.url, ping_interval=20, ping_timeout=20
            ) as ws:
                await ws.send(json.dumps(sub))
                start_time = asyncio.get_event_loop().time()

                logger.debug(f"Collecting shipping for {duration}s")
                logger.debug(f"{label}")

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
                            await asyncio.to_thread(
                                self.db.update_ship_static_data, mmsi, meta, body
                            )
                            static_count += 1

                        # --- Handle Position Reports ---
                        elif m_type == "PositionReport":
                            body = msg.get("Message", {}).get("PositionReport", {})
                            # Offload blocking DB call to a thread
                            await asyncio.to_thread(
                                self.db.update_ship_position_data, mmsi, body
                            )
                            pos_count += 1

                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        logger.warning(f"WebSocket closed unexpectedly in {label}")
                        break
                    except Exception as e:
                        logger.error(f"Error processing message in {label}: {e}")

                logger.debug(f"Updated {static_count} static, {pos_count} positions")

        except Exception as e:
            logger.error(f"Connection error for region {label}: {e}")

    async def run(self):
        import random

        # Base duration (e.g., 300s). This will be multiplied by the weight.
        base_duration = self.settings.get("listen_duration", 300)
        sleep_between_runs = self.settings.get("sleep_interval", 60)
        track_expiry = self.settings.get("vessel_track_expiry_days", 30)

        num_chunks = 10
        slice_width = 36.0

        while True:
            self.refresh_settings()

            if self.settings.get("enabled", False):
                logger.info(
                    "Shipping Collector Service: Starting weighted global rotation"
                )

                start_total = self.db.get_current_ship_total()

                try:
                    # Remove expired shipping tracks
                    self.db.prune_vessel_tracks(track_expiry)

                    # Random starting slice
                    start_offset = random.randrange(num_chunks)
                    chunk_indices = [
                        (start_offset + i) % num_chunks for i in range(num_chunks)
                    ]

                    for i in chunk_indices:
                        # Get slice metadata
                        meta = SLICE_DENSITY_MAP.get(i)
                        weight = meta["weight"]

                        # Calculate dynamic duration
                        effective_duration = int(base_duration * weight)

                        lon_start = -180.0 + (i * slice_width)
                        lon_end = lon_start + slice_width

                        if i == num_chunks - 1:
                            lon_end = 180.0

                        chunk_bbox = [[-90.0, lon_start], [90.0, lon_end]]
                        chunk_label = f"Slice {i} [{meta['label']}]"

                        # Log the specific duration for transparency in journalctl
                        logger.info(f"{chunk_label}")

                        await self.collect_ships_in_region(
                            chunk_bbox, effective_duration, chunk_label
                        )

                    end_total = self.db.get_current_ship_total()
                    logger.info(
                        f"Rotation complete. Added {end_total - start_total} new vessels."
                    )

                except Exception as e:
                    logger.error(f"Unexpected error in collection loop: {e}")
                    await asyncio.sleep(30)
            else:
                logger.debug("Shipping Collector Service disabled. Skipping.")

            await asyncio.sleep(sleep_between_runs)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    collector = ShippingCollector(args.config)
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
