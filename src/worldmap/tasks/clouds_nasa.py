#!/usr/bin/env python3
import os
import sys
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class NasaCloudUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Clouds_NASA", map_data)
        self.set_output_path()

    def run(self):
        """Downloads the cloud layer from NASA GIBS with caching logic."""
        self.exit_if_disabled()

        base_url = self.settings.get("url").strip('"')

        # NASA GIBS availability logic
        now_utc = datetime.now(timezone.utc)
        # We use yesterday's date to ensure the global mosaic is complete
        target_date = now_utc - timedelta(days=1)
        time_param = target_date.strftime("%Y-%m-%d")

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
            "WIDTH": str(self.target_width),
            "HEIGHT": str(self.target_height),
            "TIME": time_param,
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{base_url}?{query_string}"

        # --- Cache Logic ---
        # We check three things:
        # 1. Does the file exist?
        # 2. Is the file from the same 'NASA day' we are looking for?
        # 3. Has the config (resolution) changed?

        file_exists = os.path.exists(self.output_path)
        is_same_day = False

        if file_exists:
            # Check the file's modification time
            file_mtime = datetime.fromtimestamp(os.path.getmtime(self.output_path), tz=timezone.utc)
            # If the file was downloaded today (local time) and matches the target date logic, skip
            if file_mtime.date() == now_utc.date():
                is_same_day = True

        if file_exists and is_same_day and not self.config.has_changed:
            logger.info(f"NASA clouds for {time_param} are already cached and up to date.")
            return

        # --- Execution ---
        try:
            os.makedirs(str(os.path.dirname(self.output_path)), exist_ok=True)
            logger.info(f"Fetching NASA GIBS clouds for {time_param} ({self.target_width}x{self.target_height})...")

            req = urllib.request.Request(
                full_url, headers={"User-Agent": "WorldMap-Cloud-Fetcher/1.0"}
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
                with open(self.output_path, "wb") as f:
                    f.write(data)

            logger.debug(f"NASA cloud map successfully saved: {self.output_path}")

        except urllib.error.HTTPError as e:
            logger.error(f"NASA GIBS returned an error: {e.code} {e.reason}")
            # Don't exit 1 if we have a cached version we can fall back on
            if not file_exists:
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to download NASA clouds: {e}")
            if not file_exists:
                sys.exit(1)
