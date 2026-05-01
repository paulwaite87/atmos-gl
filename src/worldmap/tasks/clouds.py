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
from .common import Updater

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class CloudUpdater(Updater):
    def __init__(self, config: WorldMapConfig):
        super().__init__(config, "Clouds")

    def _generate_temp_conf(self, outfile, width, height):
        """Generates the temporary INI file required by the cloudmap library."""
        p = Path(outfile)
        dest_dir = str(p.parent)
        dest_file = p.name

        temp_conf_path = os.path.join(self.workdir, "data", "cloud_map.conf")

        temp_config = configparser.ConfigParser()
        temp_config["xplanet"] = {
            "destinationdir": dest_dir,
            "destinationfile": dest_file,
            "width": str(width),
            "height": str(height),
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
            # 1. Extract values
            outfile = self.settings.get("outfile")
            width = self.settings.getint("width")
            height = self.settings.getint("height")
            force = self.settings.getboolean("force", fallback=False)

            # 2. Create the bridging config file
            temp_conf_path = self._generate_temp_conf(outfile, width, height)
            logger.debug(f"Generated bridge config: {temp_conf_path}")

            # 3. Prepare sys.argv for the library
            prog_name = re.sub(r"(-script\.pyw|\.exe)?$", "", sys.argv[0])

            # Start with the basic config file argument
            new_args = [prog_name, f"--conf_file={temp_conf_path}"]

            # Append the --forced flag if the config setting is True
            if force:
                logger.debug("Forced update enabled")
                new_args.append("--force")

            sys.argv = new_args

            # 4. Hand over control to the library
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
