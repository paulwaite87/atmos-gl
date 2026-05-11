#!/usr/bin/env python3
import os
import re
import sys
import logging
import configparser
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# The third-party library
from cloudmap.create_map import main as cloudmap_main
from .common import Updater, MapData

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class CloudUpdater(Updater):
    """
    Downloads a cloud map for xplanet using the cloud map provided by https://clouds.matteason.co.uk/.
    This package can be installed by pip from https://pypi.org/project/CreateCloudMap/.

    The script automatically checks, if a new image is available. The default behavior is to only
    download the image, if it is new.
    """
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Clouds", map_data)
        self.set_output_path()

    def _generate_temp_conf(self):
        """Generates the temporary INI file required by the cloudmap library."""
        outfile_path = Path(self.output_path)
        temp_conf_path = str(os.path.join(self.workdir, "data", "cloud_map.conf"))

        temp_config = configparser.ConfigParser()
        temp_config["xplanet"] = {
            "destinationdir": str(outfile_path.parent),
            "destinationfile": outfile_path.name,
            "width": str(self.target_width),
            "height": str(self.target_height),
        }

        # Ensure the directory exists
        os.makedirs(os.path.dirname(temp_conf_path), exist_ok=True)
        with open(temp_conf_path, "w") as f:
            temp_config.write(f)

        return temp_conf_path

    def run(self):
        """Prepares the environment and executes the cloudmap generator."""

        # Skip this task if not enabled
        self.exit_if_disabled()

        try:
            # Extract values
            force = self.settings.getboolean("force", fallback=False)

            # Create the bridging config file
            temp_conf_path = self._generate_temp_conf()
            logger.debug(f"Generated clouds config {temp_conf_path}")

            # Prepare sys.argv for the CreateCloudMap library
            prog_name = re.sub(r"(-script\.pyw|\.exe)?$", "", sys.argv[0])

            # Start with the basic config file argument
            new_args = [prog_name, f"--conf_file={temp_conf_path}"]

            # Append the --forced flag if the config setting is True
            if force:
                logger.debug("Forced update enabled")
                new_args.append("--force")

            sys.argv = new_args

            # Hand over control to the library
            logger.debug("Starting cloud map generation...")

            # Target the specific logger
            external_logger = logging.getLogger("create_map_logger")

            # Clear any existing handlers the library might have added
            if external_logger.hasHandlers():
                external_logger.handlers.clear()

            # Set the level to ERROR
            external_logger.setLevel(logging.ERROR)

            # Disable propagation so it doesn't send messages to your root logger
            external_logger.propagate = False

            f_stdout = io.StringIO()
            f_stderr = io.StringIO()

            try:
                with redirect_stdout(f_stdout), redirect_stderr(f_stderr):
                    # Download the clouds image
                    cloudmap_main()
            finally:
                # Get the captured text
                out_content = f_stdout.getvalue()
                err_content = f_stderr.getvalue()

                # Log or process the output
                if out_content:
                    for line in out_content.splitlines():
                        logger.debug(f"[CloudMap STDOUT] {line}")

                if err_content:
                    for line in err_content.splitlines():
                        logger.error(f"[CloudMap STDERR] {line}")

        except Exception as e:
            logger.error(f"Error during cloud map generation: {e}")
            sys.exit(1)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Cloud Updater")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = CloudUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
