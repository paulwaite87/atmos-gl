#!/usr/bin/env python3
import os
import sys
import logging
import subprocess

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class CompositeUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.cloud_settings = config.get_section("clouds")
        self.isobar_settings = config.get_section("isobars")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

    def run(self):
        """Combines the isobar overlay onto the cloud map background."""
        # Retrieve paths from different config sections
        try:
            cloud_map = self.cloud_settings.get("outfile")
            isobar_map = self.isobar_settings.get("outfile")
            output_rel_path = self.isobar_settings.get(
                "composite_outfile", fallback="data/cloud_map_with_isobars.jpg"
            )

            # Resolve absolute paths using workdir
            bg_path = os.path.join(self.workdir, cloud_map)
            overlay_path = os.path.join(self.workdir, isobar_map)
            dest_path = os.path.join(self.workdir, output_rel_path)

        except (AttributeError, KeyError) as e:
            logger.error(f"Missing required config keys for composite: {e}")
            sys.exit(1)

        # Validation: Verify source files exist
        for label, path in [("Cloud map", bg_path), ("Isobar map", overlay_path)]:
            if not os.path.exists(path):
                logger.error(f"Source file missing ({label}): {path}")
                # We exit 1 here because the final map will be incomplete without this step
                sys.exit(1)

        # Construct and execute ImageMagick command
        # Syntax: composite <overlay> <background> <output>
        cmd = ["composite", overlay_path, bg_path, dest_path]

        try:
            logger.info(f"Compositing {isobar_map} onto {cloud_map}...")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"Successfully created composite: {dest_path}")

        except subprocess.CalledProcessError as e:
            logger.error(f"ImageMagick composite failed: {e.stderr}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error during composite: {e}")
            sys.exit(1)


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
