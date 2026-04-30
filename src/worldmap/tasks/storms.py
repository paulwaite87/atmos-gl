#!/usr/bin/env python3
import os
import io
import sys
import logging
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# Internal library import
from worldmap.lib.config import WorldMapConfig

logger = logging.getLogger(__name__)


class StormUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("storms")
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")

    def _get_active_csv_url(self):
        """Scrapes the NOAA IBTrACS directory for the 'ACTIVE' CSV file."""
        directory_url = self.settings.get("url")
        try:
            response = requests.get(directory_url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "ACTIVE" in href.upper() and href.endswith(".csv"):
                    return directory_url.rstrip("/") + "/" + href
        except Exception as e:
            raise RuntimeError(f"Failed to scrape storm directory: {e}")

        raise FileNotFoundError("Could not find ACTIVE CSV file on NOAA servers.")

    def run(self):
        """Fetches storm tracks and generates XPlanet markers with trails."""
        outfile = self.settings.get("outfile")
        marker_color = self.settings.get("marker_color", fallback="red")
        marker_symbol = self.settings.get("marker_symbol")
        regional_only = self.settings.getboolean("regional_only", fallback=False)
        expiry_days = self.settings.getint("expiry_days", fallback=7)

        # Resolve paths
        output_path = os.path.join(self.workdir, outfile)

        # If these markers are being skipped we ensure the marker file
        # exists to avoid xplanet warnings, and we truncate existing data
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("Shipping task disabled. Skipping.")
            # Truncate existing markers
            with open(output_path, "w") as _:
                pass
            return

        now = datetime.now(timezone.utc)
        try:
            active_url = self._get_active_csv_url()
            logger.debug(f"Downloading storm data from: {active_url}")
            response = requests.get(active_url, timeout=30)
            response.raise_for_status()

            # Process CSV
            df = pd.read_csv(
                io.StringIO(response.text),
                header=0,
                low_memory=False,
                encoding="utf-8-sig",
            )
            df = df[df["SID"] != "SID"]  # Drop unit row

            df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
            df["LON"] = pd.to_numeric(df["LON"], errors="coerce")
            df["NAME"] = df["NAME"].astype(str).str.strip()

            # Parse and localize time
            df["ISO_TIME"] = pd.to_datetime(
                df["ISO_TIME"], format="%Y-%m-%d %H:%M:%S", errors="coerce"
            )
            df["ISO_TIME"] = df["ISO_TIME"].dt.tz_localize("UTC")

            # Filter for freshness (The "Global Purge")
            latest_times = df.groupby("SID")["ISO_TIME"].transform("max")
            is_fresh = (now - latest_times) <= timedelta(days=expiry_days)
            df = df[is_fresh].copy()

            # Geographic Filtering
            if regional_only:
                lat_mask = (df["LAT"] <= 0) & (df["LAT"] >= -90)
                lon_mask = (df["LON"] >= 130) | (df["LON"] <= -120)
                df = df[lat_mask & lon_mask].copy()

            if df.empty:
                logger.debug("No active storms found within expiry window.")
                with open(output_path, "w") as f:
                    pass
                return

            # Sort and identify the lead marker vs trail
            df = df.sort_values(by=["SID", "ISO_TIME"])
            df["is_last"] = ~df.duplicated(subset=["SID"], keep="last")

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                for _, row in df.iterrows():
                    if row["is_last"] and pd.notnull(row["ISO_TIME"]):
                        date_label = row["ISO_TIME"].strftime("%d/%m")
                        label = f'"{row["NAME"]} ({date_label})"'
                        image = f"image={marker_symbol}"
                    elif row["is_last"]:
                        label = f'"{row["NAME"]}"'
                        image = ""
                    else:
                        # Trail point: just a dot, no label or icon
                        label = '""'
                        image = ""

                    f.write(
                        f"{row['LAT']} {row['LON']} {label} color={marker_color} {image}\n"
                    )

            logger.info(
                f"Storm markers generated for {df['SID'].nunique()} active storms."
            )

        except Exception as e:
            logger.error(f"Error updating storm markers: {e}")
            sys.exit(1)


def main():
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="WorldMap Storm Track Updater")
    parser.add_argument("--config", required=True, help="Path to worldmap.conf")
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = StormUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
