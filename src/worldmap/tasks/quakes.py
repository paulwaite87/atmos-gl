#!/usr/bin/env python3
import os
import io
import sys
import logging
import requests
import pandas as pd

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater

logger = logging.getLogger(__name__)


class QuakeUpdater(Updater):
    def __init__(self, config: WorldMapConfig):
        super().__init__(config, "Quakes")
        self.set_output_path()

    def run(self):
        """Fetches USGS quake data and generates an XPlanet marker file."""
        self.exit_if_disabled()

        url = self.settings.get("url")
        marker_color = self.settings.get("marker_color", fallback="white")
        marker_symbol = self.settings.get("marker_symbol")
        label_size = self.settings.get("label_fontsize", fallback="12")
        min_mag = self.settings.getfloat("min_mag", fallback=5.0)

        try:
            logger.debug(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            # Load CSV data into Pandas
            df = pd.read_csv(io.StringIO(r.text))

            # Filter by magnitude
            filtered_df = df[df["mag"] >= min_mag]

            with open(self.output_path, "w") as f:
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

            logger.debug(f"Earthquake update complete. Updated {len(filtered_df)} quakes.")

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
