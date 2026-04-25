#!/usr/bin/env python3
import os
import sys
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class NasaCloudUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("clouds_nasa")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

    def run(self):
        """Downloads the cloud layer from NASA GIBS WMS."""
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("NASA Clouds task disabled. Skipping.")
            return

        base_url = self.settings.get("url").strip('"')  # Handle quoted URLs from config
        outfile = self.settings.get("outfile")
        width = self.settings.getint("width", fallback=2048)
        height = self.settings.getint("height", fallback=1024)

        # Construct full output path
        output_path = os.path.join(self.workdir, outfile)

        # NASA's 'Best' layer availability logic
        now_utc = datetime.now(timezone.utc)
        # We subtract 24 hours for the TIME parameter to ensure a complete composite
        time_param = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
        # Log date is just for user info
        display_date = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%d")

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            "LAYERS": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
            "FORMAT": "image/jpeg",
            "TRANSPARENT": "FALSE",
            "STYLES": "",
            "SRS": "EPSG:4326",
            "BBOX": "-180,-90,180,90",
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "TIME": time_param,
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{base_url}?{query_string}"

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            logger.info(
                f"Fetching NASA GIBS clouds for {display_date} (Target: {width}x{height})"
            )

            req = urllib.request.Request(
                full_url, headers={"User-Agent": "WorldMap-Cloud-Fetcher/1.0"}
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                with open(output_path, "wb") as f:
                    f.write(response.read())

            logger.info(f"NASA cloud map successfully saved: {output_path}")

        except urllib.error.HTTPError as e:
            logger.error(f"NASA GIBS returned an error: {e.code} {e.reason}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to download NASA clouds: {e}")
            sys.exit(1)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap NASA Cloud Updater")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = NasaCloudUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
