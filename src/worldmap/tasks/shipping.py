#!/usr/bin/env python3
import json
import logging
import math

# Internal library imports
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.shipping import Ship
from worldmap.lib.db import Database
from .common import Updater, MapData, adjust_bbox_for_aspect_ratio

logger = logging.getLogger(__name__)


def get_distance_km(lat1, lon1, lat2, lon2):
    """Haversine formula to calculate distance between two points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class ShippingUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data):
        super().__init__(config, "Shipping", map_data)
        self.set_output_path()
        self.xplanet_settings = self.config.get_section("xplanet")

    def normalize_lon_for_bbox(self, lon, lon_min):
        """Shifts longitude into the 360-degree window starting at lon_min."""
        return None if not lon else (lon - lon_min) % 360 + lon_min

    async def run(self):
        self.config.load()
        self.exit_if_disabled()

        ship_db = Database()
        map_region_name = self.xplanet_settings.get("region", fallback=None)
        expiry = self.settings.getint("expiry_days", fallback=7)

        # Resolve and Adjust BBox (Mirroring renderer.py)
        bbox = self.map_data.region.bbox

        # Setup Filters and Config
        show_tracks = self.settings.getboolean("show_tracks", fallback=False)
        track_min_dist = float(self.settings.get("track_min_distance_km", fallback=5.0))
        track_max_points = self.settings.getint("track_max_points", fallback=10)

        show_ships_underway = self.settings.getboolean("show_ships_underway", fallback=False)
        show_ship_icons = self.settings.get("show_ship_icons", fallback=None)
        show_ship_classes = json.loads(self.settings.get("filter_show_ship_classes", fallback='["Tanker", "Cargo"]'))
        show_names_classes = json.loads(self.settings.get("filter_show_names_for_classes", fallback='["Tanker"]'))
        show_ships_by_name = json.loads(self.settings.get("filter_show_ships_by_name", fallback='[]'))
        min_length = self.settings.getint("filter_ships_minimum_length", fallback=0)
        base_label_fontsize = float(self.settings.getint("label_fontsize", fallback=12))
        label_color_default = self.settings.get("marker_color", fallback="red")

        fleet = ship_db.get_fleet(map_region_name, expiry_days=expiry)
        written_count = 0

        with open(self.output_path, "w") as f:
            for vessel in fleet:
                ship = Ship(vessel)

                # Length Filter
                ship_length, ship_beam = ship.get_vessel_dimensions()
                if ship_length < min_length:
                    continue

                # Class Filter
                if show_ship_classes and ship.vessel_class not in show_ship_classes:
                    continue

                # Names filter
                if show_ships_by_name and ship.vessel_name not in show_ships_by_name:
                    continue

                # Ship underway filter
                if show_ships_underway and not ship.is_underway():
                    continue

                raw_lat, raw_lon = ship.get_vessel_position()
                if raw_lat is None or raw_lon is None:
                    continue

                # Default colour
                marker_colour = ""

                # --- Coordinate Normalization ---
                ship_latitude = raw_lat
                ship_longitude = raw_lon

                # Formatting and Marker Writing
                ship_colour = ship.get_vessel_colour()

                if ship.vessel_class in show_names_classes:
                    fontsize = int(base_label_fontsize)
                    ship_expanded_class = ship.get_expanded_vessel_class()

                    if ship_expanded_class == "ULTRA":
                        fontsize = int(base_label_fontsize * 2.0)
                    elif ship_expanded_class == "VLCC":
                        fontsize = int(base_label_fontsize * 1.6)
                    elif ship_expanded_class == "STD":
                        fontsize = int(base_label_fontsize * 1.3)

                    ship_label = f"{ship.get_vessel_description()}"
                    fontsize = f" fontsize={fontsize}"
                    marker_colour = f" color={ship_colour}"
                else:
                    ship_label = fontsize = ""

                # Ship icon image logic
                if show_ship_icons == "Arrows":
                    marker_image = f"image={ship.get_vessel_directional_icon()}"
                elif show_ship_icons == "Discs":
                    marker_image = f" image={ship.get_vessel_disc_icon()}"
                else:
                    marker_image = ""
                    marker_colour = f" color={ship_colour}"

                # --- Write the Primary Ship Marker ---
                f.write(f'{ship_latitude} {ship_longitude} "{ship_label}"{fontsize}{marker_colour}{marker_image}\n')
                written_count += 1

                # --- Track Normalization ---
                if show_tracks:
                    history = ship_db.get_ship_track(ship.mmsi, limit=100)
                    last_lat, last_lon = ship_latitude, ship_longitude
                    points_placed = 0

                    for pos in history:
                        if points_placed >= track_max_points:
                            break

                        h_lat = float(pos['lat'])
                        h_lon = float(pos['lon'])

                        dist = get_distance_km(last_lat, last_lon, h_lat, h_lon)
                        if dist >= track_min_dist:
                            # Only write track points if they are within our bbox
                            if self.map_data.region.is_in_region(h_lat, h_lon):
                                f.write(f"{h_lat} {h_lon} color={label_color_default}\n")
                                last_lat, last_lon = h_lat, h_lon
                                points_placed += 1

        logger.info(f"Shipping update complete. Placed {written_count} ships in region.")