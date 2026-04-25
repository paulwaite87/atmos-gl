#!/usr/bin/env python3
import os
import io
import sys
import logging
import requests
import pandas as pd

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class QuakeUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("quakes")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

    def run(self):
        """Fetches USGS quake data and generates an XPlanet marker file."""
        url = self.settings.get("url")
        outfile = self.settings.get("outfile")
        marker_color = self.settings.get("marker_color", fallback="white")
        marker_symbol = self.settings.get("marker_symbol")
        label_size = self.settings.get("label_fontsize", fallback="12")
        min_mag = self.settings.getfloat("min_mag", fallback=5.0)

        # Resolve paths, ensure directory
        output_path = os.path.join(self.workdir, outfile)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # If these markers are being skipped we ensure the marker file
        # exists to avoid xplanet warnings, and we truncate existing data
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("Quakes task disabled. Skipping.")
            # Truncate existing markers
            with open(output_path, "w") as _:
                pass
            return

        try:
            logger.info(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            # Load CSV data into Pandas
            df = pd.read_csv(io.StringIO(r.text))

            # Filter by magnitude
            filtered_df = df[df["mag"] >= min_mag]

            with open(output_path, "w") as f:
                for _, row in filtered_df.iterrows():
                    mag = row["mag"]
                    depth = int(row["depth"])
                    # Format: lat lon "label" color=X fontsize=Y image=Z
                    line = (
                        f"{row['latitude']} {row['longitude']} "
                        f'"M{mag} {depth}km" color={marker_color} '
                        f"fontsize={label_size} image={marker_symbol}\n"
                    )
                    f.write(line)

            logger.info(
                f"Successfully wrote {len(filtered_df)} quake markers to: {output_path}"
            )

        except requests.RequestException as e:
            logger.error(f"Network error fetching quakes: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error in quakes task: {e}")
            sys.exit(1)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Quake Marker Updater")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = QuakeUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
