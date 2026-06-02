#!/usr/bin/env python3
import json
import logging
import urllib.error
import urllib.request

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class VolcanoUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Volcanoes", map_data)
        self.set_output_path()

    def _fetch_volcano_data(self, base_url, page_size=200):
        """Fetch all records from the NOAA HazEL API with pagination."""
        items = []
        page = 1
        try:
            while True:
                url = f"{base_url}?page={page}&itemsPerPage={page_size}"
                req = urllib.request.Request(
                    url, headers={"Accept": "application/json"}
                )

                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    batch = data.get("items", [])
                    if not batch:
                        break
                    items.extend(batch)

                    # Stop if we've reached the total count reported by API
                    if len(items) >= data.get("count", 0):
                        break
                    page += 1
            return items
        except Exception as e:
            logger.error(f"Error connecting to NOAA HazEL API: {e}")
            return []

    def run(self):
        """Processes volcano records and generates markers."""
        self.exit_if_disabled()

        base_url = self.get_base_url()
        marker_color = self.settings.get("marker_color", "red")
        marker_symbol = self.settings.get("marker_symbol")
        significant_only = self.settings.get("significant_only", False)
        show_volcanoes_by_name = self.settings.get("filter_show_volcanoes_by_name", [])
        vei_min = self.settings.get("vei_min", 5)
        erupt_codes = self.settings.get("erupt_date_codes", ["D1"])

        logger.debug(f"Fetching volcano data (VEI >= {vei_min})...")
        records = self._fetch_volcano_data(base_url)
        if not records:
            logger.warning("No volcano records retrieved.")
            return

        count = 0
        with open(self.output_path, "w") as f:
            for r in records:
                name = r.get("name", "Unknown")

                if show_volcanoes_by_name and name not in show_volcanoes_by_name:
                    continue

                lat = r.get("latitude")
                lon = r.get("longitude")
                significant = r.get("significant", False)
                last_erupt = r.get("timeErupt", "")
                vei = r.get("vei", 0)

                # Filter logic
                if (
                    (significant or not significant_only)
                    and (last_erupt in erupt_codes)
                    and (vei >= vei_min)
                ):
                    if lat is not None and lon is not None:
                        # Format: lat lon "label" color=X image=Y
                        f.write(
                            f'{lat} {lon} "{name}" color={marker_color} image={marker_symbol}\n'
                        )
                        count += 1

        logger.debug(f"Volcanoes update complete. Updated {count} volcanoes.")
