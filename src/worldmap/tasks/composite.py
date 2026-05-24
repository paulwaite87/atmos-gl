#!/usr/bin/env python3
import os
import sys
import logging
from pathlib import Path

# Need Pillow for the transparency and compositing
from PIL import Image

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, COMPOSITE_SECTIONS

logger = logging.getLogger(__name__)


class CompositeUpdater(Updater):
    """
    Joins the enabled weather layers (SST, Clouds, Precipitation, Isobars, Wind) into a single map.
    Layers are applied dynamically bottom-to-top based on configuration.
    """

    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Composite", map_data)
        self.set_output_path()

        # Config sections
        self.clouds_settings = self.config.get_section("clouds")
        self.sst_settings = self.config.get_section("sst")
        self.precip_settings = self.config.get_section("precipitation")
        self.isobar_settings = self.config.get_section("isobars")
        self.wind_settings = self.config.get_section("wind")
        self.currents_settings = self.config.get_section("currents")
        self.waves_settings = self.config.get_section("waves")
        self.temperature_settings = self.config.get_section("temperature")

        # Enabled flags
        self.sst_enabled = self.config.section_enabled("sst")
        self.clouds_enabled = self.clouds_settings.getboolean("enabled", fallback=False)
        self.precip_enabled = self.config.section_enabled("precipitation")
        self.isobars_enabled = self.config.section_enabled("isobars")
        self.wind_enabled = self.config.section_enabled("wind")
        self.currents_enabled = self.config.section_enabled("currents")
        self.waves_enabled = self.config.section_enabled("waves")
        self.temperature_enabled = self.config.section_enabled("temperature")
        self.storms_enabled = self.config.section_enabled("storms")

    def _apply_cloud_transparency(self, cloud_img: Image.Image) -> Image.Image:
        """
        Applies threshold and gamma corrections to the cloud mask
        to prevent 'white-out' and control wispy-ness.
        """
        threshold = self.clouds_settings.getint("threshold", fallback=0)
        gamma = self.clouds_settings.getfloat("gamma", fallback=1.0)

        cloud_mask = cloud_img.convert("L")

        lut = [
            int(pow(i / 255.0, 1.0 / gamma) * 255.0) if i >= threshold else 0
            for i in range(256)
        ]
        cloud_mask = cloud_mask.point(lut)

        # Fully transparent base
        base = Image.new("RGBA", cloud_img.size, (0, 0, 0, 0))
        white_clouds = Image.new("RGBA", cloud_img.size, (255, 255, 255, 255))
        base.paste(white_clouds, (0, 0), mask=cloud_mask)

        return base

    def run(self):
        """Combines the enabled weather layers onto the map background."""
        self.exit_if_disabled()

        logger.debug("Starting composite updater")

        try:
            logger.debug(f"Creating weather map image => {self.output_path}")

            # Define expected paths for the regional cache
            cloud_filename = f"clouds_{self.map_data.region.region_identifier}_{self.target_width}x{self.target_height}.jpg"
            cloud_map_path = os.path.join(self.workdir, "data", "regions", cloud_filename)

            regional_cloud_map = str(os.path.join(
                self.workdir,
                "data",
                "regions",
                f"clouds_transparent_{self.map_data.region.region_identifier}_{self.target_width}x{self.target_height}.png"
            ))

            # Prepare the cloud base if enabled and the cached region file exists
            if self.clouds_enabled and os.path.exists(cloud_map_path):
                # We skip self.get_regional_image() because the file is already regional
                with Image.open(cloud_map_path) as raw_clouds_image:
                    transparent_clouds = self._apply_cloud_transparency(raw_clouds_image)
                    logger.debug(f"Saving transparent regional cloud map in {regional_cloud_map}")
                    transparent_clouds.save(regional_cloud_map, "PNG")

        except (AttributeError, KeyError) as e:
            logger.error(f"Missing required config keys for composite: {e}")
            sys.exit(1)

        # --- Dynamic Compositing Logic ---
        layers = []
        for section in COMPOSITE_SECTIONS:
            if self.config.section_enabled(section):
                # Inject our specialized regional cloud path if processing clouds
                if section == "clouds":
                    if os.path.exists(regional_cloud_map):
                        layers.append((section, regional_cloud_map))
                else:
                    section_image_path = self.get_output_path_if_exists(section)
                    if section_image_path:
                        layers.append((section, section_image_path))

        if not layers:
            logger.debug("No composite layers enabled. Skipping.")
            return

        # Validate files exist
        for label, path in layers:
            if not os.path.exists(path):
                logger.error(f"Source file missing ({label}): {path}")
                sys.exit(1)

        # Case: Compositing process
        try:
            logger.debug(f"Compositing layers: {[l[0] for l in layers]}...")

            # Use target dimensions from the MapData object to create a standardized canvas.
            # This prevents clipping when a single layer's aspect ratio differs from the background.
            target_size = (self.target_width, self.target_height)
            bg_img = Image.new("RGBA", target_size, (0, 0, 0, 0))

            for label, path in layers:
                with Image.open(path) as overlay_img:
                    overlay_img = overlay_img.convert("RGBA")

                    # Resize to match the global project dimensions using high-quality resampling
                    if overlay_img.size != target_size:
                        overlay_img = overlay_img.resize(target_size, Image.Resampling.LANCZOS)

                    # Paste layer using its own alpha channel as the mask
                    bg_img.paste(overlay_img, (0, 0), mask=overlay_img)

            # Save final standardized output
            bg_img.save(self.output_path, "PNG")
            logger.debug(f"Successfully created composite: {self.output_path}")

        except Exception as e:
            logger.error(f"Unexpected error during PIL composite: {e}")
            sys.exit(1)