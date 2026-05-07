#!/usr/bin/env python3
import os
import sys
import shutil
import logging
import subprocess
from pathlib import Path
# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class CompositeUpdater(Updater):
    """
    Joins the cloud map with the isobar map. Either cloud, or isobars, or
    both may be disabled, and we cater for all cases. If both are disabled,
    the output file will be removed. If either or both are enabled, the
    composite background will be created in the output file.
    """
    def __init__(self, config: WorldMapConfig, map_data):
        super().__init__(config, "Composite", map_data)
        self.set_output_path()

        # Clouds could come from either source here
        if self.config.section_enabled("clouds_nasa"):
            self.clouds_settings = self.config.get_section("clouds_nasa")
        else:
            self.clouds_settings = self.config.get_section("clouds")
        self.isobar_settings = self.config.get_section("isobars")

        self.clouds_enabled = self.clouds_settings.getboolean("enabled", fallback=False)
        self.isobars_enabled = self.config.section_enabled("isobars")

    def run(self):
        """Combines the isobar overlay onto the cloud map background."""
        self.exit_if_disabled()

        logger.debug("Starting composite updater")

        # Set up all the paths
        try:
            logger.debug(f"Creating weather map image => {self.output_path}")
            isobar_map_path = str(os.path.join(self.workdir, self.isobar_settings.get("outfile")))
            cloud_map_path = str(os.path.join(self.workdir, self.clouds_settings.get("outfile")))
            regional_cloud_map = ""
            if self.clouds_enabled:
                # Transform the clouds for region, or just
                # leave as-is if no region is defined
                p = Path(cloud_map_path)
                regional_cloud_map = str(os.path.join(
                    self.workdir, 
                    "data",
                    "regions",
                    f"{p.stem}_{self.map_data.region.region_identifier}{p.suffix}"
                ))
                clouds_image = self.get_regional_image(cloud_map_path)
                logger.debug(f"Saving regional cloud maps in {regional_cloud_map}")
                clouds_image.save(regional_cloud_map, "JPEG", quality=90)
        except (AttributeError, KeyError) as e:
            logger.error(f"Missing required config keys for composite: {e}")
            sys.exit(1)

        # Overlay case - both updaters are enabled
        if self.clouds_enabled and self.isobars_enabled:
            for label, path in [("Cloud map", cloud_map_path), ("Isobar map", isobar_map_path)]:
                if not os.path.exists(path):
                    logger.error(f"Source file missing ({label}): {path}")
                    sys.exit(1)
            try:
                logger.debug(f"Compositing {isobar_map_path} onto {regional_cloud_map}...")
                # Syntax: composite <overlay> <background> <output>
                subprocess.run(
                    ["composite", isobar_map_path, regional_cloud_map, self.output_path],
                    check=True,
                    capture_output=True,
                    text=True
                )
                logger.debug(f"Successfully created composite: {self.output_path}")

            except subprocess.CalledProcessError as e:
                logger.error(f"ImageMagick composite failed: {e.stderr}")
                sys.exit(1)

            except Exception as e:
                logger.error(f"Unexpected error during composite: {e}")
                sys.exit(1)

        # Only clouds
        elif self.clouds_enabled:
            if not os.path.exists(regional_cloud_map):
                logger.error(f"Source file missing: {regional_cloud_map}")
                sys.exit(1)
            shutil.copyfile(regional_cloud_map, self.output_path)

        # Only isobars
        elif self.isobars_enabled:
            if not os.path.exists(isobar_map_path):
                logger.error(f"Source file missing: {isobar_map_path}")
                sys.exit(1)
            shutil.copyfile(isobar_map_path, self.output_path)

        else:
            pass


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Image Compositor")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = CompositeUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
