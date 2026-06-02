#!/usr/bin/env python3
import json
import logging

# Internal library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.shipping import Ship
from worldmap.lib.db import Database
from .common import Updater
from worldmap.lib.logging import set_loglevel

logger = logging.getLogger(__name__)
set_loglevel("DEBUG")


class ShippingUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data):
        super().__init__(config, "Shipping", map_data)

    async def run(self):
        self.exit_if_disabled()

        ship_db = Database()
        map_region_name = self.config.get_setting("common", "region")
        expiry = self.settings.get("expiry_days", 7)

        # Filters
        show_ships_underway = self.settings.get("filter_ships_underway", False)
        show_ship_classes = self.settings.get("filter_show_ship_classes", [])
        show_ships_by_name = self.settings.get("filter_show_ships_by_name", "")
        show_ships_min_length = self.settings.get("filter_ships_minimum_length", 0)

        fleet = ship_db.get_fleet(map_region_name, expiry_days=expiry)
        ships_list = []

        for vessel in fleet:
            ship = Ship(vessel)

            # --- Apply Filters ---
            ship_length, ship_beam = ship.get_vessel_dimensions()
            if ship_length < show_ships_min_length:
                continue
            if show_ship_classes and ship.vessel_class not in show_ship_classes:
                continue
            if show_ships_by_name and ship.vessel_name not in show_ships_by_name:
                continue
            if show_ships_underway and not ship.is_underway():
                continue

            raw_lat, raw_lon = ship.get_vessel_position()
            if raw_lat is None or raw_lon is None:
                continue

            # Build the rich dictionary for the frontend tooltip
            ship_data = {
                "lat": raw_lat,
                "lng": raw_lon,
                "mmsi": ship.mmsi,
                "name": ship.vessel_name or "Unknown Vessel",
                "type": ship.vessel_class or "Unknown",
                "expanded_type": ship.get_expanded_vessel_class(),
                "length": ship_length,
                "beam": ship_beam,
                "status": "Underway" if ship.is_underway() else "Moored/Anchored",
                "color_base": ship.get_vessel_color_name(),
                "heading": ship.get_vessel_16point_angle()  # Sending the raw angle to the UI
            }

            ships_list.append(ship_data)

        # Write the JSON payload
        with open(self.output_path, "w") as f:
            json.dump(ships_list, f, indent=2)

        logger.info(f"Shipping update complete. Placed {len(ships_list)} ships.")