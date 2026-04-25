#!/usr/bin/env python3
import os
import json
import logging
import asyncio
import websockets
from websockets.exceptions import ConnectionClosed

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class ShippingUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("shipping")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

        # Path resolution
        db_rel = config.get_section("shipping_harvester").get("static_database")
        self.full_db_path = os.path.join(self.workdir, db_rel)
        self.output_path = os.path.join(self.workdir, self.settings.get("outfile"))

    def load_cache(self):
        """Safely loads the ship database with error recovery."""
        if os.path.exists(self.full_db_path):
            if os.path.getsize(self.full_db_path) == 0:
                return {}
            try:
                with open(self.full_db_path, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                bak = self.full_db_path + ".broken"
                logger.error(f"Cache corrupted. Moving to {bak}")
                os.rename(self.full_db_path, bak)
        return {}

    def save_cache(self, cache_data):
        os.makedirs(os.path.dirname(self.full_db_path), exist_ok=True)
        with open(self.full_db_path, "w") as f:
            json.dump(cache_data, f, indent=4)

    async def _get_ais_stream(self, url, subscription, duration):
        """Internal helper for websocket streaming."""
        messages = []
        try:
            async with websockets.connect(url, close_timeout=1) as ws:
                await ws.send(json.dumps(subscription))
                start = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start < duration:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        messages.append(json.loads(msg))
                    except asyncio.TimeoutError:
                        continue
                    except ConnectionClosed:
                        break
        except Exception as e:
            if "no close frame" not in str(e):
                logger.error(f"WebSocket failure: {e}")
        return messages

    async def run(self):
        # If these markers are being skipped we ensure the marker file
        # exists to avoid xplanet warnings, and we truncate existing data
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("Shipping task disabled. Skipping.")
            # Truncate existing markers
            with open(self.output_path, "w") as _:
                pass
            return

        api_key = self.settings.get("api_key")
        url = self.settings.get("url")
        bbox = json.loads(self.settings.get("bbox"))

        # Load Cache
        ship_cache = self.load_cache()
        selected_ships = {}

        # --- Phase 1: Positions ---
        logger.info(
            f"Streaming AIS positions for {self.settings.get('listen_for_positional_data')}s..."
        )
        pos_sub = {
            "APIKey": api_key,
            "BoundingBoxes": bbox,
            "FilterMessageTypes": ["PositionReport"],
        }
        raw_pos = await self._get_ais_stream(
            url, pos_sub, self.settings.getint("listen_for_positional_data")
        )

        show_active = self.settings.getboolean("show_only_active_ships", fallback=False)
        ship_types = json.loads(
            self.settings.get("show_ship_types", fallback='["Tanker", "Cargo"]')
        )

        for msg in raw_pos:
            if msg.get("MessageType") != "PositionReport":
                continue

            meta = msg.get("MetaData", {})
            m_data = msg.get("Message", {}).get("PositionReport", {})
            mmsi = str(meta.get("MMSI", ""))

            sog = m_data.get("Sog", 0.0)
            status = m_data.get("NavigationalStatus", 15)

            if not show_active or (sog > 1.0 and status not in [1, 5]):
                cached = ship_cache.get(mmsi, {})
                v_type = cached.get("type", 0)
                name = (cached.get("name") or meta.get("ShipName", "Unknown")).strip()

                is_tanker = 80 <= v_type <= 89 or any(
                    w in name.upper() for w in ["TANKER", "OIL", "LPG"]
                )
                is_cargo = 70 <= v_type <= 79

                if (is_tanker and "Tanker" in ship_types) or (
                    is_cargo and "Cargo" in ship_types
                ):
                    selected_ships[mmsi] = {
                        "lat": meta.get("latitude"),
                        "lon": meta.get("longitude"),
                        "name": name,
                        "type_name": "Tanker" if is_tanker else "Cargo",
                        "sog": sog,
                        "is_tanker": is_tanker,
                        "is_cargo": is_cargo,
                        "draught": cached.get("draught", 0.0),
                        "prev_draught": cached.get("prev_draught", 0.0),
                    }

        # --- Phase 2: Static Data ---
        if selected_ships:
            logger.info(
                f"Streaming AIS static data for {self.settings.get('listen_for_static_data')}s..."
            )
            static_sub = {
                "APIKey": api_key,
                "BoundingBoxes": bbox,
                "FilterMessageTypes": ["ShipStaticData"],
            }
            raw_static = await self._get_ais_stream(
                url, static_sub, self.settings.getint("listen_for_static_data")
            )

            updated = False
            for msg in raw_static:
                if msg.get("MessageType") != "ShipStaticData":
                    continue
                m = msg.get("MetaData", {})
                b = msg.get("Message", {}).get("ShipStaticData", {})
                mid = str(m.get("MMSI") or b.get("UserID", ""))

                if mid in selected_ships:
                    new_d = b.get("MaximumStaticDraught", 0.0)
                    curr = ship_cache.get(mid, {})
                    prev_d = curr.get("draught", 0.0)

                    if 0.0 < prev_d != new_d != 0.0:
                        prev_d = curr["draught"]

                    selected_ships[mid]["draught"] = (
                        new_d
                        if new_d != 0.0
                        else (selected_ships[mid]["draught"] or prev_d)
                    )
                    selected_ships[mid]["prev_draught"] = prev_d

                    ship_cache[mid] = {
                        "name": m.get("ShipName")
                        or b.get("Name")
                        or selected_ships[mid]["name"],
                        "type": b.get("Type") or b.get("VesselType") or v_type,
                        "draught": selected_ships[mid]["draught"],
                        "prev_draught": selected_ships[mid]["prev_draught"],
                    }
                    updated = True

            if updated:
                self.save_cache(ship_cache)

        # --- Phase 3: Write Markers ---
        m_color = self.settings.get("marker_color", fallback="red")
        name_types = json.loads(
            self.settings.get("show_ship_names_for_types", fallback='["Tanker"]')
        )
        label_size = self.settings.get("label_fontsize", fallback="12")


        with (open(self.output_path, "w") as f):
            for mmsi, info in selected_ships.items():
                # Select Symbol
                suffix = (
                    "_empty.png"
                    if (0 < info["draught"] < info["prev_draught"] > 0)
                    else ".png"
                )
                prefix = "ship_tanker" if info["is_tanker"] else "ship_cargo"
                symbol = f"{prefix}{suffix}"

                color = "white" if info["sog"] > 20 else m_color
                label = ""
                if info["type_name"] in name_types:
                    label = info["name"].replace('"', "")
                    label = f'"{label}" color={color} fontsize={label_size}'

                f.write(
                    f'{info["lat"]} {info["lon"]} {label} image={symbol}\n'
                )

        logger.info(f"Shipping update complete. {len(selected_ships)} markers written.")


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = ShippingUpdater(config)
    asyncio.run(updater.run())


if __name__ == "__main__":
    main()
