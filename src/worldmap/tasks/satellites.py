#!/usr/bin/env python3
import logging
import requests
import os

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, listify

logger = logging.getLogger(__name__)


class SatelliteUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Satellites", map_data)
        self.set_output_path()

    def run(self):
        """Fetches CelesTrak TLE data and formats it for XPlanet."""
        self.exit_if_disabled()

        base_url = self.get_base_url()
        target_names = listify(self.settings.get("sat_names", fallback=""))

        if not target_names:
            logger.debug("No satellites configured in names list. Skipping.")
            return

        logger.debug(f"Satellites to track: {target_names}")

        # We will fetch the raw text endpoints from CelesTrak for our target groups
        groups = ["stations", "weather"]
        all_tle_lines = []
        network_failure = False

        # Fetch all data
        for group in groups:
            # Construct the proper query structure: gp.php?GROUP=xyz&FORMAT=tle
            query_url = f"{base_url}/gp.php?GROUP={group}&FORMAT=tle"
            try:
                logger.debug(f"Fetching satellite data from {query_url}...")
                r = requests.get(query_url, timeout=15)
                r.raise_for_status()
                all_tle_lines.extend(r.text.splitlines())
            except requests.RequestException as e:
                logger.error(f"NETWORK ERROR: Failed to fetch {query_url}. API may be rate-limiting or offline. Details: {e}")
                network_failure = True

        if not all_tle_lines:
            if network_failure:
                logger.error(f"CRITICAL: Satellite API fetch failed completely. '{self.output_path}' remains truncated. Satellites will be hidden until the connection recovers.")
            else:
                logger.warning(f"No satellite data retrieved from API. '{self.output_path}' remains truncated.")
            return

        # Filter and write in XPlanet 3-line format
        found_sats = 0
        try:
            logger.debug(f"Pre-run size of {self.output_path}: {os.path.getsize(self.output_path)}")
            with open(self.output_path, "w") as f:
                # TLEs are 3 lines: Name, Line 1, Line 2
                for i in range(0, len(all_tle_lines), 3):
                    # Prevent index out of bounds on malformed files
                    if i + 2 >= len(all_tle_lines):
                        break

                    # CelesTrak pads names with spaces, so we must strip it
                    name_line = all_tle_lines[i].strip()
                    line1 = all_tle_lines[i + 1].strip()
                    line2 = all_tle_lines[i + 2].strip()

                    # Our list of names can be a substring of the acquired name
                    if any(name in name_line for name in target_names):
                        # Append the XPlanet formatting flags to the title line
                        xplanet_name_line = f"0 {name_line} [color=White,trail=max,trail_color=Cyan]"

                        f.write(f"{xplanet_name_line}\n")
                        f.write(f"{line1}\n")
                        f.write(f"{line2}\n")
                        found_sats += 1

            logger.info(f"Satellite update complete. Tracked {found_sats}/{len(target_names)} objects.")

            # Warn if we didn't find everything we asked for
            if found_sats < len(target_names):
                missing = len(target_names) - found_sats
                logger.warning(f"Could not find TLE data for {missing} configured satellite(s). Check spelling in config.")

            logger.debug(f"Post-run size of {self.output_path}: {os.path.getsize(self.output_path)}")

        except OSError as e:
            logger.error(f"Failed to write satellite marker file: {e}")