#!/usr/bin/env python3
"""AIS WebSocket stream -> database (ship positions and static data).

Long-running async collector: maintains a WebSocket connection to the AIS stream and
processes incoming messages in a density-weighted global rotation. Runs as its own
Docker service because the blocking GFS downloads in DataCollector.collect_once() would
starve its event loop if merged into data_collector (follow-on: asyncio.to_thread).

Moved from src/atmos_gl/shipping_collector.py to src/atmos_gl/collectors/shipping.py to
live under the shared collectors umbrella. Core logic is unchanged.
"""
import os
import json
import logging
import asyncio
import websockets

from .base import AsyncCollectorBase
from atmos_gl.db.ship_adapter import ShipAdapter

logger = logging.getLogger(__name__)

# 10 longitude slices with density weights: >1.0 = more time, <1.0 = quick pass.
SLICE_DENSITY_MAP = {
    0: {"label": "Mid-Pacific (East)", "weight": 0.3},           # -180 to -144
    1: {"label": "Eastern Pacific / Americas West", "weight": 0.5}, # -144 to -108
    2: {"label": "Americas East / Panama / Caribbean", "weight": 1.5}, # -108 to -72
    3: {"label": "Western Atlantic", "weight": 0.8},              # -72 to -36
    4: {"label": "Eastern Atlantic / Gibraltar", "weight": 1.5},  # -36 to 0
    5: {"label": "Europe / West Africa / Mediterranean", "weight": 2.0}, # 0 to 36
    6: {"label": "Middle East / Suez / Hormuz / Aden", "weight": 2.0},  # 36 to 72
    7: {"label": "Indian Ocean / Bay of Bengal", "weight": 1.0},  # 72 to 108
    8: {"label": "SE Asia / Malacca / South China Sea", "weight": 2.0}, # 108 to 144
    9: {"label": "Australia / NZ / Japan / West Pacific", "weight": 0.9}, # 144 to 180
}


class ShippingCollector(AsyncCollectorBase):
    section = "shipping_collector"
    datasource_key = "shipping"

    def __init__(self, config_path: str):
        super().__init__(config_path)
        self.ship_adapter = ShipAdapter()

    @property
    def heartbeat_period_s(self) -> float:
        """Expected gap between heartbeats. run() records one after EVERY slice (not just
        once per full rotation - with real-world settings a full rotation can take hours,
        which would leave the Data Status UI showing "never" for a healthy collector), so
        this is the longest a single slice can run (listen_duration * the heaviest weight)
        rather than the full rotation sum."""
        base = float(self.settings.get("listen_duration", 300))
        max_weight = max(m["weight"] for m in SLICE_DENSITY_MAP.values())
        return base * max_weight

    def _sleep_interval_seconds(self) -> float:
        """shipping_collector.sleep_interval (the "Sleep interval" slider, 5-30) is
        stored/edited in MINUTES; run()'s pause between rotations needs seconds."""
        try:
            minutes = int(self.settings.get("sleep_interval", 5))
        except (TypeError, ValueError):
            minutes = 5
        minutes = min(30, max(5, minutes))
        return minutes * 60.0

    def refresh_settings(self) -> None:
        super().refresh_settings()
        # Cached (not resolved fresh at each connect) since collect_ships_in_region()
        # is called once per slice, ten times a rotation -- self.url is derived from
        # source_url() (the same method the Data Status link uses) rather than a second
        # independent config read, so the two can't silently disagree.
        self.url = self.source_url() or ""
        # API key: config file first, then environment variable.
        self.api_key = (
            self.settings.get("api_key")
            or os.environ.get("AIS_API_KEY")
        )
        if not self.api_key:
            logger.error("ShippingCollector: no AIS API key found in config or AIS_API_KEY env var.")

    async def collect_ships_in_region(self, bbox, duration, label):
        """Connect to AIS stream and process messages for a bounding box."""
        if not self.api_key:
            logger.warning(f"Skipping {label}: no API key.")
            return

        sub = {
            "APIKey": self.api_key,
            "BoundingBoxes": [bbox],
            "FilterMessageTypes": [
                "ShipStaticData",
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
            ],
        }

        static_count = pos_count = 0
        try:
            async with websockets.connect(
                self.url, ping_interval=20, ping_timeout=20
            ) as ws:
                await ws.send(json.dumps(sub))
                start_time = asyncio.get_event_loop().time()
                logger.debug(f"Collecting shipping for {duration}s — {label}")

                while asyncio.get_event_loop().time() - start_time < duration:
                    try:
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg = json.loads(msg_raw)
                        m_type = msg.get("MessageType")
                        meta = msg.get("MetaData", {})
                        mmsi = str(meta.get("MMSI") or "")
                        if not mmsi:
                            continue

                        if m_type == "ShipStaticData":
                            body = msg.get("Message", {}).get("ShipStaticData", {})
                            await asyncio.to_thread(
                                self.ship_adapter.update_ship_static_data, mmsi, meta, body, "A"
                            )
                            static_count += 1
                        elif m_type in (
                            "PositionReport",
                            "StandardClassBPositionReport",
                            "ExtendedClassBPositionReport",
                        ):
                            tier = "A" if m_type == "PositionReport" else "B"
                            body = msg.get("Message", {}).get(m_type, {})
                            await asyncio.to_thread(
                                self.ship_adapter.update_ship_position_data, mmsi, meta, body, tier
                            )
                            pos_count += 1

                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        logger.warning(f"WebSocket closed in {label}; reconnecting next slice.")
                        break
                    except Exception as exc:
                        logger.error(f"Message error in {label}: {exc}")

                logger.debug(f"{label}: {static_count} static, {pos_count} position updates.")

        except Exception as exc:
            logger.error(f"Connection error for {label}: {exc}")

    async def run(self) -> None:
        import random

        # Startup heartbeat: a real rotation can take hours (10 slices * listen_duration *
        # weight), and the Data Status UI should show "the collector is alive" the moment
        # this task starts, not leave a blank "never" until the first full rotation
        # completes. Percent naturally decays from here if nothing else follows within
        # heartbeat_period_s, so this can't paper over a real hang.
        self.process_status_adapter.record_process_run(self.section, "collector", success=True)

        while True:
            self.refresh_settings()

            if self.enabled:
                base_duration = int(self.settings.get("listen_duration", 300))
                sleep_between_runs = self._sleep_interval_seconds()
                num_chunks = 10
                slice_width = 36.0

                logger.info("ShippingCollector: starting weighted global rotation.")
                start_total = self.ship_adapter.get_current_ship_total()

                try:
                    start_offset = random.randrange(num_chunks)
                    for i in ((start_offset + j) % num_chunks for j in range(num_chunks)):
                        meta = SLICE_DENSITY_MAP[i]
                        lon_start = -180.0 + (i * slice_width)
                        lon_end = 180.0 if i == num_chunks - 1 else lon_start + slice_width
                        bbox = [[-90.0, lon_start], [90.0, lon_end]]
                        label = f"Slice {i} [{meta['label']}]"
                        logger.info(label)
                        await self.collect_ships_in_region(
                            bbox, int(base_duration * meta["weight"]), label
                        )
                        # Per-slice heartbeat (not just once at the end of the full
                        # rotation) - see heartbeat_period_s's docstring for why.
                        self.process_status_adapter.record_process_run(self.section, "collector", success=True)

                    end_total = self.ship_adapter.get_current_ship_total()
                    logger.info(
                        f"ShippingCollector: rotation complete. "
                        f"Added {end_total - start_total} vessels."
                    )
                except Exception as exc:
                    logger.error(f"ShippingCollector: loop error: {exc}")
                    self.process_status_adapter.record_process_run(
                        self.section, "collector", success=False, error=str(exc)
                    )
                    await asyncio.sleep(30)
                    continue

                await asyncio.sleep(sleep_between_runs)
            else:
                logger.debug("ShippingCollector: disabled.")
                await asyncio.sleep(60)


if __name__ == "__main__":
    ShippingCollector.main()

def main():
    ShippingCollector.main()

