#!/usr/bin/env python3
import os
import glob
import time
import logging
import subprocess

from worldmap.lib.config import WorldMapConfig
from .common import Updater

logger = logging.getLogger(__name__)


class XPlanetRenderer(Updater):
    def __init__(self, config: WorldMapConfig):
        super().__init__(config, "Xplanet")

    def run(self):
        """Executes XPlanet to render the final map image."""
        self.exit_if_disabled()

        # Resolve paths relative to workdir
        data_dir = os.path.join(self.workdir, "data")
        x_conf = os.path.join(
            self.workdir,
            self.settings.get("xplanet_conf", fallback="config/xplanet.conf"),
        )
        logger.debug(f"XPlanet conf: {x_conf}")
        base_name = self.settings.get("base_filename", fallback="worldmap.jpg")

        # Rendering parameters
        geometry = self.settings.get("geometry", fallback="1920x1080")
        projection = self.settings.get("projection", fallback="rectangular")
        longitude = self.settings.get("longitude", fallback="175")

        # Cleanup old map files to prevent storage bloat
        search_pattern = os.path.join(data_dir, f"*-{base_name}")
        old_files = glob.glob(search_pattern)
        for old_file in old_files:
            try:
                os.remove(old_file)
                logger.debug(f"Cleaned up old map: {old_file}")
            except Exception as e:
                logger.warning(f"Could not remove {old_file}: {e}")

        # Generate new filename with Unix timestamp for the web dashboard/frontend
        timestamp = int(time.time())
        output_path = os.path.join(data_dir, f"{timestamp}-{base_name}")

        # 3. Build XPlanet command
        cmd = [
            "xplanet",
            "-conf",
            x_conf,
            "-searchdir",
            self.workdir,
            "-projection",
            projection,
            "-geometry",
            geometry,
            "-longitude",
            longitude,
            "-output",
            output_path,
            "-num_times",
            "1",
        ]
        logger.debug(f"Running XPlanet command: {' '.join(cmd)}")

        try:
            logger.debug(f"Rendering {geometry} map via XPlanet...")
            # We use a timeout to ensure XPlanet doesn't hang the whole pipeline
            result = subprocess.run(
                cmd,
                check=True,
                timeout=60,
                capture_output=True,
                text=True,
                cwd=self.workdir,
            )
            # Log the output even on success to see warnings
            if result.stderr:
                logger.warning(f"XPlanet Warnings: {result.stderr}")

            logger.debug(f"Final map generated: {output_path}")
        except subprocess.TimeoutExpired:
            logger.error("XPlanet timed out after 60 seconds.")
        except subprocess.CalledProcessError as e:
            logger.error(f"XPlanet failed (exit {e.returncode}): {e.stderr}")
        except Exception as e:
            logger.error(f"Unexpected error during XPlanet execution: {e}")


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap XPlanet Renderer")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    renderer = XPlanetRenderer(config)
    renderer.run()


if __name__ == "__main__":
    main()
