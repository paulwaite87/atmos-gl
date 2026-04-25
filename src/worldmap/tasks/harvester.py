#!/usr/bin/env python3
import os
import sys
import json
import logging
import asyncio
import websockets
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class ShipHarvester:
    settings = None
    ship_database = None

    def __init__(self, config_path):
        self.config = WorldMapConfig(config_path)
        self.workdir = self.config.get_section("common").get("workdir", ".")
        logger.info(f"Workdir: {self.workdir} Now getting settings")
        self.load_settings()

    def load_settings(self):
        self.config.load()
        self.settings = self.config.get_section("shipping_harvester")
        db_rel = self.settings.get("static_database")
        self.ship_database = str(os.path.join(self.workdir, db_rel))

    def load_db(self):
        if os.path.exists(self.ship_database) and os.path.getsize(self.ship_database) > 0:
            try:
                with open(self.ship_database, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Corrupt database at {self.ship_database}")
        return {}

    def save_db(self, data):
        os.makedirs(os.path.dirname(self.ship_database), exist_ok=True)
        with open(self.ship_database, "w") as f:
            json.dump(data, f, indent=4)

    async def run(self):
        self.load_settings()

        url = self.settings.get("url")
        api_key = self.settings.get("api_key")
        bbox = json.loads(self.settings.get("bbox"))
        duration = self.settings.getint("duration", fallback=300)

        db = self.load_db()
        initial_count = len(db)
        logger.info(f"Initial ship count: {initial_count}")
        sub = {
            "APIKey": api_key,
            "BoundingBoxes": bbox,
            "FilterMessageTypes": ["ShipStaticData"],
        }

        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps(sub))
                start_time = asyncio.get_event_loop().time()

                while asyncio.get_event_loop().time() - start_time < duration:
                    try:
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg = json.loads(msg_raw)

                        if msg.get("MessageType") == "ShipStaticData":
                            m = msg.get("MetaData", {})
                            b = msg.get("Message", {}).get("ShipStaticData", {})
                            mmsi = str(m.get("MMSI") or b.get("UserID", ""))

                            if mmsi:
                                db[mmsi] = {
                                    "name": m.get("ShipName", b.get("Name", "Unknown")),
                                    "type": b.get("Type", b.get("VesselType", 0)),
                                    "imo": b.get("ImoNumber", 0),
                                    "callsign": b.get("CallSign", "").strip(),
                                    "draught": b.get("MaximumStaticDraught", 0.0),
                                }
                    except asyncio.TimeoutError:
                        continue

            self.save_db(db)
            logger.info(
                f"Harvest complete. Total records: {len(db)} (+{len(db) - initial_count})"
            )
        except Exception as e:
            logger.error(f"Harvester connection error: {e}")
            sys.exit(1)
