#!/usr/bin/env python3
import os
import sys
import shutil
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

    def __init__(self, config: WorldMapConfig, map_data):
        super().__init__(config, "Composite", map_data)
        self.set_output_path()

        # Config sections
        if self.config.section_enabled("clouds_nasa"):
            self.clouds_settings = self.config.get_section("clouds_nasa")
        else:
            self.clouds_settings = self.config.get_section("clouds")

        self.sst_settings = self.config.get_section("sst")
        self.precip_settings = self.config.get_section("precipitation")
        self.isobar_settings = self.config.get_section("isobars")
        self.wind_settings = self.config.get_section("wind")

        # Enabled flags
        self.sst_enabled = self.config.section_enabled("sst")
        self.clouds_enabled = self.clouds_settings.getboolean("enabled", fallback=False)
        self.precip_enabled = self.config.section_enabled("precipitation")
        self.isobars_enabled = self.config.section_enabled("isobars")
        self.wind_enabled = self.config.section_enabled("wind")

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
            sst_map_path = str(os.path.join(self.workdir, self.sst_settings.get("outfile", "")))
            cloud_map_path = str(os.path.join(self.workdir, self.clouds_settings.get("outfile", "")))
            precip_map_path = str(os.path.join(self.workdir, self.precip_settings.get("outfile", "")))
            isobar_map_path = str(os.path.join(self.workdir, self.isobar_settings.get("outfile", "")))
            wind_map_path = str(os.path.join(self.workdir, self.wind_settings.get("outfile", "")))

            regional_cloud_map = ""

            # Prepare the cloud base if enabled
            if self.clouds_enabled:
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
        # Note the order: SST is at the bottom, Wind is at the top.
        layers = []

        if self.sst_enabled:
            layers.append(("SST", sst_map_path))

        if self.clouds_enabled:
            layers.append(("Clouds", regional_cloud_map))

        if self.precip_enabled:
            layers.append(("Precipitation", precip_map_path))

        if self.isobars_enabled:
            layers.append(("Isobars", isobar_map_path))

        if self.wind_enabled:
            layers.append(("Wind", wind_map_path))

        if not layers:
            logger.debug("No composite layers enabled. Skipping.")
            return

        # Validate files exist
        for label, path in layers:
            if not os.path.exists(path):
                logger.error(f"Source file missing ({label}): {path}")
                sys.exit(1)

        # Case 1: Only a single layer is enabled
        if len(layers) == 1:
            label, path = layers[0]
            logger.debug(f"Only {label} enabled. Copying to output.")
            shutil.copyfile(path, self.output_path)
            return

        # Case 2: Compositing process
        try:
            logger.debug(f"Compositing layers: {[l[0] for l in layers]}...")

            # Open first layer as canvas
            bottom_label, bottom_path = layers[0]
            with Image.open(bottom_path) as bg_img:
                bg_img = bg_img.convert("RGBA")

                # Paste subsequent layers
                for label, path in layers[1:]:
                    with Image.open(path) as overlay_img:
                        overlay_img = overlay_img.convert("RGBA")

                        if overlay_img.size != bg_img.size:
                            overlay_img = overlay_img.resize(bg_img.size, Image.Resampling.LANCZOS)

                        # Overlaying using the image's own alpha channel
                        bg_img.paste(overlay_img, (0, 0), mask=overlay_img)

                # Save final output as PNG
                bg_img.save(self.output_path, "PNG")

            logger.debug(f"Successfully created composite: {self.output_path}")

        except Exception as e:
            logger.error(f"Unexpected error during PIL composite: {e}")
            sys.exit(1)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Image Compositor")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = CompositeUpdater(config, None)
    updater.run()


if __name__ == "__main__":
    main()