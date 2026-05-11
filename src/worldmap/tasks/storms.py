#!/usr/bin/env python3
import os
import logging
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class StormUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Storms", map_data)
        self.set_output_path()
        # Persistent CSV cache
        self.csv_path = os.path.join(self.workdir, "data/active_storms.csv")

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

    def download_if_newer(self):
        """Downloads the CSV only if the remote file is newer than local cache."""
        try:
            active_url = self._get_active_csv_url()
            response = requests.head(active_url, timeout=10)
            response.raise_for_status()

            remote_mtime_str = response.headers.get('Last-Modified')
            remote_mtime = None
            if remote_mtime_str:
                remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                    tzinfo=timezone.utc)

            file_exists = os.path.exists(self.csv_path)
            if file_exists and remote_mtime:
                local_mtime = datetime.fromtimestamp(os.path.getmtime(self.csv_path), tz=timezone.utc)
                if remote_mtime <= local_mtime:
                    logger.info("Storm CSV cache is up to date.")
                    return False

            logger.info(f"Downloading fresh storm data from {active_url}")
            r = requests.get(active_url, timeout=30)
            r.raise_for_status()

            os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
            with open(self.csv_path, "wb") as f:
                f.write(r.content)
            return True

        except Exception as e:
            logger.warning(f"Metadata check failed for storm data: {e}")
            return False

    def run(self):
        """Fetches storm tracks and generates XPlanet markers."""
        self.exit_if_disabled()

        # Bandwidth check
        new_data = self.download_if_newer()
        marker_file_exists = os.path.exists(self.output_path)

        # Only process if data is new, marker file is gone, or config changed
        if not (new_data or not marker_file_exists or self.config.has_changed):
            logger.info("Storm markers are up to date. Skipping.")
            return

        # Process the local CSV
        if not os.path.exists(self.csv_path):
            logger.error("No storm data available to plot.")
            return

        try:
            marker_color = self.settings.get("marker_color", fallback="red")
            marker_symbol = self.settings.get("marker_symbol")
            regional_only = self.settings.getboolean("regional_only", fallback=False)
            expiry_days = self.settings.getint("expiry_days", fallback=7)
            now = datetime.now(timezone.utc)

            df = pd.read_csv(self.csv_path, header=0, low_memory=False, encoding="utf-8-sig")
            df = df[df["SID"] != "SID"]  # Drop unit row

            df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
            df["LON"] = pd.to_numeric(df["LON"], errors="coerce")
            df["NAME"] = df["NAME"].astype(str).str.strip()
            df["ISO_TIME"] = pd.to_datetime(df["ISO_TIME"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
            df["ISO_TIME"] = df["ISO_TIME"].dt.tz_localize("UTC")

            # Filter for freshness
            latest_times = df.groupby("SID")["ISO_TIME"].transform("max")
            df = df[(now - latest_times) <= timedelta(days=expiry_days)].copy()

            if regional_only:
                # Custom mask for your Tasman/Pacific region
                lat_mask = (df["LAT"] <= 10) & (df["LAT"] >= -60)
                lon_mask = (df["LON"] >= 100) | (df["LON"] <= -120)
                df = df[lat_mask & lon_mask].copy()

            if df.empty:
                # Clear the marker file if no storms are active
                open(self.output_path, 'w').close()
                logger.debug("No active storms found. Cleared marker file.")
                return

            df = df.sort_values(by=["SID", "ISO_TIME"])
            df["is_last"] = ~df.duplicated(subset=["SID"], keep="last")

            with open(self.output_path, "w") as f:
                for _, row in df.iterrows():
                    if row["is_last"] and pd.notnull(row["ISO_TIME"]):
                        date_label = row["ISO_TIME"].strftime("%d/%m")
                        label = f'"{row["NAME"]} ({date_label})"'
                        image = f"image={marker_symbol}" if marker_symbol else ""
                    else:
                        label = '""'
                        image = ""

                    f.write(f"{row['LAT']} {row['LON']} {label} color={marker_color} {image}\n")

            logger.info(f"Storm markers updated: {df['SID'].nunique()} systems tracked.")

        except Exception as e:
            logger.error(f"Error processing storm markers: {e}")


if __name__ == "__main__":
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = WorldMapConfig(args.config)
    updater = StormUpdater(config, None)
    updater.run()