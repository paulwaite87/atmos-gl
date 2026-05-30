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


class CloudUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Clouds", map_data)

        # Override default output path to save directly to the regional cache
        filename = f"clouds_{self.map_data.region.region_identifier}_{self.target_width}x{self.target_height}.jpg"
        self.output_path = os.path.join(self.workdir, "data", "regions", filename)

    def run(self):
        """Downloads the regional cloud layer from NASA GIBS with a baseline lookback."""
        self.exit_if_disabled()

        base_url = self.get_base_url()
        expiry_hours = self.settings.getint("expiry_hours", fallback=3)

        # --- NEW: Configurable lookback to prevent incomplete satellite swaths ---
        # Default to 1 day back, but can be set to 2 in worldmap.conf if needed
        cloud_offset = self.settings.getint("offset_days", fallback=1)

        now_utc = datetime.now(timezone.utc)

        # --- Align with GFS baseline if available, but apply the lookback ---
        baseline = getattr(self.map_data, "shared_state", {}).get("gfs_baseline")
        if baseline:
            # We must offset from the baseline because GIBS cannot provide "today" in full yet.
            target_date = baseline["timestamp"] - timedelta(days=cloud_offset)
            logger.debug(
                f"Clouds syncing to Isobar baseline with a -{cloud_offset} day offset: {target_date.strftime('%Y-%m-%d')}"
            )
        else:
            target_date = now_utc - timedelta(days=cloud_offset)

        time_param = target_date.strftime("%Y-%m-%d")

        # Dynamically construct the Bounding Box for the target region using the bbox list
        # bbox is [lon_min, lat_min, lon_max, lat_max]
        bbox_str = f"{self.map_data.region.bbox[0]},{self.map_data.region.bbox[1]},{self.map_data.region.bbox[2]},{self.map_data.region.bbox[3]}"

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            "LAYERS": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
            "FORMAT": "image/jpeg",
            "TRANSPARENT": "FALSE",
            "STYLES": "",
            "SRS": "EPSG:4326",
            "BBOX": bbox_str,
            "WIDTH": str(self.target_width),
            "HEIGHT": str(self.target_height),
            "TIME": time_param,
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{base_url}?{query_string}"

        # --- Cache Logic ---
        # Only download if the file does not exist OR the file is older than the expiry limit
        if os.path.exists(self.output_path):
            file_mtime = datetime.fromtimestamp(
                os.path.getmtime(self.output_path), tz=timezone.utc
            )
            age = now_utc - file_mtime

            if age < timedelta(hours=expiry_hours):
                logger.info(
                    f"NASA clouds cached file is fresh ({age.total_seconds() / 3600:.1f} hours old). Skipping download."
                )
                return

        # --- Execution ---
        try:
            os.makedirs(str(os.path.dirname(self.output_path)), exist_ok=True)
            logger.info(
                f"Fetching regional NASA GIBS clouds for {time_param} ({self.target_width}x{self.target_height})..."
            )

            req = urllib.request.Request(
                full_url, headers={"User-Agent": "WorldMap-Cloud-Fetcher/1.0"}
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
                with open(self.output_path, "wb") as f:
                    f.write(data)

            logger.debug(
                f"NASA regional cloud map successfully saved: {self.output_path}"
            )

        except urllib.error.HTTPError as e:
            logger.error(f"NASA GIBS returned an error: {e.code} {e.reason}")
            if not os.path.exists(self.output_path):
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to download NASA clouds: {e}")
            if not os.path.exists(self.output_path):
                sys.exit(1)
