#!/usr/bin/env python3
import os
import json
import logging
import asyncio

# Internal library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.shipping import (
    ShipDatabase,
    get_vessel_class,
    get_vessel_subclass,
    get_vessel_dimensions,
    get_vessel_description,
    get_vessel_navigational_status,
    get_vessel_position,
    vessel_is_underway, get_expanded_vessel_class,
)

logger = logging.getLogger(__name__)


class ShippingUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("shipping")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

        # Path resolution for the marker file
        self.output_path = os.path.join(self.workdir, self.settings.get("outfile"))

    async def run(self):
        self.config.load()  # Refresh config
        ship_db = ShipDatabase()

        region_list = json.loads(self.settings.get("regions", fallback="[]"))
        expiry = self.settings.getint("expiry_days", fallback=3)

        logger.debug(f"Generating map for regions: {region_list or 'GLOBAL'} (Expiry: {expiry} days)")

        fleet = ship_db.get_fleet(region_labels=region_list, expiry_days=expiry)

        # Filter and Format Markers
        show_ships_underway = self.settings.get("show_ships_underway", fallback="False")
        show_ship_classes = json.loads(
            self.settings.get("filter_show_ship_classes", fallback='["Tanker", "Cargo"]')
        )
        show_names_classes = json.loads(
            self.settings.get("filter_show_names_for_classes", fallback='["Tanker"]')
        )
        min_length = self.settings.getint("filter_ships_minimum_length", fallback=0)

        label_color_default = self.settings.get("marker_color", fallback="red")
        base_label_fontsize = float(self.settings.getint("label_fontsize", fallback=12))

        written_count = 0
        with open(self.output_path, "w") as f:
            for ship in fleet:
                # Basic Metadata from DB
                ship_class = get_vessel_class(ship)

                # Length Filter
                ship_length, ship_beam = get_vessel_dimensions(ship)
                if ship_length < min_length:
                    continue

                # Class Filter
                if len(show_ship_classes) > 0 and ship_class not in show_ship_classes:
                    continue

                # Ship underway filter
                if show_ships_underway and not vessel_is_underway(ship):
                    continue

                # Formatting coordinates for Xplanet
                # In your DB these are stored as 'lat' and 'lon' floats
                ship_latitude, ship_longitude = get_vessel_position(ship)
                if ship_latitude is None or ship_longitude is None:
                    continue

                # Logic for "Empty Ship" symbol (draught comparison)
                draught = float(ship["draught"]) or 0.0
                prev_draught = float(ship["prev_draught"]) or 0.0
                is_empty = (0.0 < draught < prev_draught > 0.0)

                suffix = "_empty.png" if is_empty else ".png"
                if ship_class == "Tanker":
                    prefix = "ship_tanker"
                elif ship_class == "Cargo":
                    prefix = "ship_cargo"
                else:
                    prefix = "ship"
                    suffix = ".png"

                ship_symbol = f"{prefix}{suffix}"

                # Label Logic
                ship_label = ""
                if ship_class in show_names_classes:
                    label_color = label_color_default
                    label_size = int(base_label_fontsize)

                    # Gets the enhanced class description
                    ship_expanded_class = get_expanded_vessel_class(ship)

                    # Marker scaling and colours for Tankers
                    if ship_expanded_class == "ULTRA":
                        label_size = int(base_label_fontsize * 2.0)
                    elif ship_expanded_class == "VLCC":
                        label_size = int(base_label_fontsize * 1.6)
                        label_color = "DeepPink"
                    elif ship_expanded_class == "STD":
                        label_size = int(base_label_fontsize * 1.3)
                        label_color = "Green"

                    ship_label = (f'"{get_vessel_description(ship)}" '
                                  f'color={label_color} fontsize={label_size}')

                # Final Write to Xplanet file
                f.write(f"{ship_latitude} {ship_longitude} {ship_label} image={ship_symbol}\n")
                written_count += 1

        logger.debug(f"Shipping update complete. {written_count} markers written to {self.output_path}.")


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