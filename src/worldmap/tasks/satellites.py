#!/usr/bin/env python3
import logging
import requests
import os
import time
import math

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, listify

logger = logging.getLogger(__name__)


class SatelliteUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Satellites", map_data)
        self.set_output_path()

    def run(self):
        """Fetches CelesTrak TLE data with localized 24-hour group file caching."""
        self.exit_if_disabled()

        base_url = self.get_base_url()
        marker_color = self.settings.get("marker_color", fallback="White")
        marker_fontsize = self.settings.getint("marker_fontsize", fallback=10)
        trail_minutes = self.settings.getint("trail_minutes", fallback=5)
        degrees_above_horizon = self.settings.getint(
            "degrees_above_horizon", fallback=45
        )
        target_names = listify(self.settings.get("sat_names", fallback=""))
        target_names += listify(self.settings.get("extra_satellite_names", fallback=""))

        if not target_names:
            logger.debug("No satellites configured in names list. Skipping.")
            return

        logger.debug(f"Satellites to track: {target_names}")

        groups = ["stations", "weather", "science", "resource"]
        all_tle_lines = []

        # Ensure the data caching directory exists
        data_dir = os.path.dirname(self.output_path) or "data"
        os.makedirs(data_dir, exist_ok=True)

        # Process each group using local cache rules
        for group in groups:
            cache_file = os.path.join(data_dir, f"celestrak_{group}.txt")
            should_download = True

            # Check if cache exists and is fresh (less than 24 hours old)
            if os.path.exists(cache_file):
                file_age_seconds = time.time() - os.path.getmtime(cache_file)
                if file_age_seconds < 86400:  # 24 hours in seconds
                    logger.debug(
                        f"Using fresh cached TLE data for '{group}' ({int(file_age_seconds / 3600)}h old)."
                    )
                    should_download = False

            if should_download:
                query_url = f"{base_url}/gp.php?GROUP={group}&FORMAT=tle"
                try:
                    logger.info(
                        f"Cache expired or missing. Fetching satellite data from {query_url}..."
                    )
                    r = requests.get(query_url, timeout=15)
                    r.raise_for_status()

                    # Save the raw group response payload to our local cache
                    with open(cache_file, "w", encoding="utf-8") as f_cache:
                        f_cache.write(r.text)

                except Exception as e:
                    logger.error(f"Failed to refresh satellite group '{group}': {e}")
                    if not os.path.exists(cache_file):
                        logger.error(
                            f"No cached file available as fallback for group '{group}'. Skipping."
                        )
                        continue
                    logger.warning(f"Falling back to stale cache for group '{group}'.")

            # Load the data from the local cache
            try:
                with open(cache_file, "r", encoding="utf-8") as f_cache:
                    all_tle_lines.extend(f_cache.readlines())
            except OSError as e:
                logger.error(f"Failed to read cache file {cache_file}: {e}")

        if not all_tle_lines:
            logger.error("No TLE data available. Skipping.")
            return

        # Parse TLE records and build output files
        try:
            found_sats = 0

            # Define both output paths. self.output_path is configured as 'satellites/sat_file'
            marker_file = self.output_path
            tle_file = f"{self.output_path}.tle"

            with open(marker_file, "w") as f_marker, open(tle_file, "w") as f_tle:
                # Iterate by 3-line blocks
                for i in range(0, len(all_tle_lines) - 2, 3):
                    name_line = all_tle_lines[i].strip()
                    line1 = all_tle_lines[i + 1].strip()
                    line2 = all_tle_lines[i + 2].strip()

                    # Ensure case-insensitive, whitespace-agnostic matching
                    if any(
                        name.strip().upper() in name_line.upper()
                        for name in target_names
                    ):
                        # Extract NORAD ID from line 2 (cols 2-7)
                        try:
                            sat_id = line2[2:7].strip()

                            # Altitude Math: Mean Motion is cols 52-63 on Line 2
                            mean_motion = float(line2[52:63].strip())
                            n = (mean_motion * 2 * math.pi) / 86400.0
                            mu = 3.986004418e14
                            a_meters = (mu / (n**2)) ** (1 / 3)
                            altitude = int((a_meters / 1000.0) - 6371.0)
                            display_name = f"{name_line} [{altitude} km]"
                        except (ValueError, IndexError):
                            sat_id = "00000"
                            display_name = name_line

                        # Write styling
                        f_marker.write(
                            f'{sat_id} "{display_name}" color={marker_color} fontsize={marker_fontsize}\n'
                        )
                        f_marker.write(
                            f'{sat_id} "" image=none altcirc={degrees_above_horizon} trail={{orbit,-{trail_minutes},0,1}} color=Yellow\n'
                        )
                        f_marker.write(
                            f'{sat_id} "" image=none trail={{orbit,{trail_minutes},0,1}} color=Red\n'
                        )

                        # --- 2. Write raw orbital math to the .tle file ---
                        f_tle.write(f"{name_line}\n")
                        f_tle.write(f"{line1}\n")
                        f_tle.write(f"{line2}\n")

                        found_sats += 1

            logger.info(
                f"Satellite update complete. Tracked {found_sats}/{len(target_names)} objects."
            )

            if found_sats < len(target_names):
                missing = len(target_names) - found_sats
                logger.warning(
                    f"Could not find TLE data for {missing} configured satellite(s). Check spelling in config."
                )

            logger.debug(
                f"Post-run marker size: {os.path.getsize(marker_file)}, TLE size: {os.path.getsize(tle_file)}"
            )

        except OSError as e:
            logger.error(f"Failed to write satellite marker and TLE files: {e}")
