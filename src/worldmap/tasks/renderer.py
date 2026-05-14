#!/usr/bin/env python3
import os
import glob
import time
import logging
import json
import subprocess

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.maps import NASAGIBSDownloader
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class XPlanetRenderer(Updater):
    def __init__(self, config: WorldMapConfig, map_data):
        super().__init__(config, "XPlanet", map_data)
        self.downloader = NASAGIBSDownloader()

        # This is the default colour Xplanet will use when it has no map image
        # Used when we specify a bbox extending beyond 180 longitude, and
        # avoids the default white being used.
        self.default_fill_colour = self.settings.get("default_fill_colour", fallback="black")

        # Ensure regional data directory exists for caching
        self.region_dir = os.path.join(self.workdir, "data", "regions")
        os.makedirs(self.region_dir, exist_ok=True)

        # Weather imagery flags
        self.composite_enabled = self.config.section_enabled("composite")
        self.clouds_enabled = self.config.section_enabled("clouds")
        self.isobars_enabled = self.config.section_enabled("isobars")
        self.wind_enabled = self.config.section_enabled("wind")
        self.precipitation_enabled = self.config.section_enabled("precipitation")
        self.sst_enabled = self.config.section_enabled("sst")

    def get_regional_maps(self):
        """Returns paths for day/night maps, downloading if missing from cache."""
        region_identifier = self.map_data.region.region_identifier
        target_width = self.map_data.region.target_width
        target_height = self.map_data.region.target_height
        region_geometry = f"{target_width}x{target_height}"
        bbox = self.map_data.region.bbox
        day_path = os.path.join(
            self.region_dir,
            f"{region_identifier}_{region_geometry}_day.jpg"
        )
        night_path = os.path.join(
            self.region_dir,
            f"{region_identifier}_{region_geometry}_night.jpg"
        )

        if not os.path.exists(day_path):
            logger.debug(f"Cache miss: Downloading {region_geometry} regional day map for {region_identifier}...")
            self.downloader.download_region_map(
                bbox,
                target_width,
                target_height,
                day_path,
                is_night=False
            )

        if not os.path.exists(night_path):
            logger.debug(f"Cache miss: Downloading {region_geometry} regional night map for {region_identifier}...")
            self.downloader.download_region_map(
                bbox,
                target_width,
                target_height,
                night_path,
                is_night=True
            )

        return day_path, night_path

    def run(self):
        """Executes XPlanet using a dynamically generated configuration file."""
        self.exit_if_disabled()

        # Setup paths and base settings
        data_dir = os.path.join(self.workdir, "data")
        base_name = self.settings.get("base_filename", fallback="regionmap.jpg")

        # Acquire the maps
        day_map, night_map = self.get_regional_maps()

        # Create the dynamic xplanet.conf
        temp_conf_path = os.path.join(data_dir, "xplanet_dynamic.conf")
        with open(temp_conf_path, "w") as f:
            f.write("[earth]\n")
            f.write('"Earth"\n')
            f.write(f'color={self.default_fill_colour}\n')
            f.write(f"map={day_map}\n")
            f.write(f"night_map={night_map}\n")
            # Xplanet mapbounds={NorthWest_Lat, NorthWest_Lon, SouthEast_Lat, SouthEast_Lon}
            # bbox order from maps.py: [lon_min, lat_min, lon_max, lat_max]
            # Therefore: {lat_max, lon_min, lat_min, lon_max}
            f.write(f"mapbounds={{{self.map_region_bbox[3]},{self.map_region_bbox[0]},{self.map_region_bbox[1]},{self.map_region_bbox[2]}}}\n")

            # Whether to display the weather
            if self.composite_enabled and (self.clouds_enabled or self.isobars_enabled or self.wind_enabled or self.precipitation_enabled or self.sst_enabled):
                f.write(f'cloud_map={self.config.get_section("composite").get("outfile")}\n')
                f.write("cloud_threshold=0\n")
                f.write("cloud_gamma=1.0\n")

            # Show active storms
            if self.config.section_enabled("storms"):
                f.write(f'marker_file={self.config.get_section_outfile("storms")}\n')

            # Show earthquake markers
            if self.config.section_enabled("quakes"):
                f.write(f'marker_file={self.config.get_section_outfile("quakes")}\n')

            # Show volcanoes
            if self.config.section_enabled("volcanoes"):
                f.write(f'marker_file={self.config.get_section_outfile("volcanoes")}\n')

            # Show shipping activity
            if self.config.section_enabled("shipping"):
                f.write(f'marker_file={self.config.get_section_outfile("shipping")}\n')

            # Additional marker files from config
            marker_files = json.loads(self.settings.get("marker_files", fallback='[]'))
            for marker_file in marker_files:
                f.write(f"marker_file={marker_file}\n")

            # Default style for markers if not specified
            f.write(f'marker_color={self.settings.get("marker_default_colour", "cyan")}\n')
            f.write(f'marker_fontsize={self.settings.getint("marker_default_fontsize", 12)}\n')

        # Cleanup old map files
        search_pattern = os.path.join(data_dir, f"*-{base_name}")
        for old_file in glob.glob(search_pattern):
            try:
                os.remove(old_file)
            except Exception as e:
                logger.warning(f"Cleanup failed for {old_file}: {e}")

        # 5. Build and Run Command
        timestamp = int(time.time())
        output_path = os.path.join(data_dir, f"{timestamp}-{base_name}")

        # This matches the clouds, isobars data which we download
        projection = "rectangular"

        cmd = [
            "xplanet",
            "-conf", temp_conf_path,
            "-searchdir", self.workdir,
            "-projection", projection,
            "-geometry", self.common.get("desktop_geometry"),
            "-latitude", str(self.centre_latitude),
            "-longitude", str(self.centre_longitude),
            "-output", output_path,
            "-num_times", "1",
        ]

        logger.debug(f"Running XPlanet: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd, check=True, timeout=60, capture_output=True, text=True, cwd=self.workdir
            )
            if result.stderr:
                logger.warning(f"XPlanet Warnings: {result.stderr}")
            logger.info(f"Successfully generated map: {output_path}")

        except subprocess.TimeoutExpired:
            logger.error("XPlanet timed out after 60 seconds.")
        except subprocess.CalledProcessError as e:
            logger.error(f"XPlanet failed (exit {e.returncode}): {e.stderr}")
        except Exception as e:
            logger.error(f"Unexpected error during render: {e}")
