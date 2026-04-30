#!/usr/bin/env python3
import os
import json
import logging
import urllib.error
import urllib.request

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class VolcanoUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("volcanoes")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

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
        """Processes volcano records and generates XPlanet markers."""
        base_url = self.settings.get("url")
        outfile = self.settings.get("outfile")
        marker_color = self.settings.get("marker_color", fallback="red")
        marker_symbol = self.settings.get("marker_symbol")
        significant_only = self.settings.getboolean("significant_only", fallback=False)
        vei_min = self.settings.getint("vei_min", fallback=5)
        # Load date codes (e.g., ["D1"] for Holocene)
        try:
            erupt_codes = json.loads(
                self.settings.get("erupt_date_codes", fallback='["D1"]')
            )
        except json.JSONDecodeError:
            erupt_codes = ["D1"]

        # Resolve paths
        output_path = os.path.join(self.workdir, outfile)

        # If these markers are being skipped we ensure the marker file
        # exists to avoid xplanet warnings, and we truncate existing data
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("Volcanoes task disabled. Skipping.")
            # Truncate existing markers
            with open(output_path, "w") as _:
                pass
            return

        logger.debug(f"Fetching volcano data (VEI >= {vei_min})...")
        records = self._fetch_volcano_data(base_url)
        if not records:
            logger.warning("No volcano records retrieved.")
            return

        count = 0
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w") as f:
            for r in records:
                lat = r.get("latitude")
                lon = r.get("longitude")
                name = r.get("name", "Unknown")
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

        logger.debug(f"Successfully wrote {count} volcano markers to: {output_path}")


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Volcano Marker Updater")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = VolcanoUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
