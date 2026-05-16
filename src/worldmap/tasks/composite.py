#!/usr/bin/env python3
import os
import sys
import logging
from pathlib import Path

# Need Pillow for the transparency and compositing
from PIL import Image

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

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
        self.clouds_section = "clouds"
        if self.config.section_enabled("clouds_nasa"):
            self.clouds_section = "clouds_nasa"

        self.clouds_settings = self.config.get_section(self.clouds_section)
        self.sst_settings = self.config.get_section("sst")
        self.precip_settings = self.config.get_section("precipitation")
        self.isobar_settings = self.config.get_section("isobars")
        self.wind_settings = self.config.get_section("wind")
        self.currents_settings = self.config.get_section("currents")
        self.waves_settings = self.config.get_section("waves")

        # Enabled flags
        self.sst_enabled = self.config.section_enabled("sst")
        self.clouds_enabled = self.clouds_settings.getboolean("enabled", fallback=False)
        self.precip_enabled = self.config.section_enabled("precipitation")
        self.isobars_enabled = self.config.section_enabled("isobars")
        self.wind_enabled = self.config.section_enabled("wind")
        self.currents_enabled = self.config.section_enabled("currents")
        self.waves_enabled = self.config.section_enabled("waves")

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

            # Source paths
            sst_map_path = self.get_output_path_if_exists("sst")
            cloud_map_path = self.get_output_path_if_exists(self.clouds_section)
            precip_map_path = self.get_output_path_if_exists("precipitation")
            isobars_map_path = self.get_output_path_if_exists("isobars")
            wind_map_path = self.get_output_path_if_exists("wind")
            currents_map_path = self.get_output_path_if_exists("currents")
            waves_map_path = self.get_output_path_if_exists("waves")

            regional_cloud_map = ""

            # Prepare the cloud base if enabled
            if self.clouds_enabled and cloud_map_path:
                p = Path(cloud_map_path)
                regional_cloud_map = str(os.path.join(
                    self.workdir,
                    "data",
                    "regions",
                    f"{p.stem}_{self.map_data.region.region_identifier}.png"
                ))

                raw_clouds_image = self.get_regional_image(cloud_map_path)
                if raw_clouds_image:
                    transparent_clouds = self._apply_cloud_transparency(raw_clouds_image)
                    logger.debug(f"Saving regional cloud maps in {regional_cloud_map}")
                    transparent_clouds.save(regional_cloud_map, "PNG")
                else:
                    logger.error("Failed to generate regional cloud image.")
                    sys.exit(1)

        except (AttributeError, KeyError) as e:
            logger.error(f"Missing required config keys for composite: {e}")
            sys.exit(1)

        # --- Dynamic Compositing Logic ---
        layers = []

        if self.sst_enabled and sst_map_path:
            layers.append(("SST", sst_map_path))

        if self.currents_enabled and currents_map_path:
            layers.append(("Currents", currents_map_path))

        if self.waves_enabled and waves_map_path:
            layers.append(("Waves", waves_map_path))

        if self.clouds_enabled and regional_cloud_map:
            layers.append(("Clouds", regional_cloud_map))

        if self.precip_enabled and precip_map_path:
            layers.append(("Precipitation", precip_map_path))

        if self.isobars_enabled and isobars_map_path:
            layers.append(("Isobars", isobars_map_path))

        if self.wind_enabled and wind_map_path:
            layers.append(("Wind", wind_map_path))

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