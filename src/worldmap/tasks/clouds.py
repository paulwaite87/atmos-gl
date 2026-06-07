#!/usr/bin/env python3
import os
import sys
import logging
import math
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from PIL import Image

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, MERCATOR_LAT_LIMIT

logger = logging.getLogger(__name__)


def _lonlat_to_mercator_m(lon, lat):
    """WGS84 lon/lat degrees -> EPSG:3857 metres, latitude clamped to the
    Mercator limit so the poles can't produce +/-inf."""
    R = 20037508.342789244  # == 6378137 * pi  (half the Mercator world span)
    lat = max(-MERCATOR_LAT_LIMIT, min(MERCATOR_LAT_LIMIT, lat))
    x = lon * R / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * R / math.pi
    return x, y


class CloudUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Clouds", map_data)

        # Configurable lookback to prevent incomplete satellite swaths
        # Default to 1 day back, but can be set to 2 in worldmap.json if needed
        self.cloud_offset = self.settings.get("offset_days", 1)

        # Override default output path to save directly to the regional cache
        cache_filename = f"clouds_{self.cloud_offset}_{self.map_data.region.region_identifier}_{self.target_width}x{self.target_height}.jpg"
        self.cache_output_path = os.path.join(self.workdir, "data", cache_filename)

    def save_cache_as_transparent(self):
        threshold = self.settings.get("threshold", 0)
        gamma = self.settings.get("gamma", 1.0)
        with Image.open(self.cache_output_path) as raw_clouds_image:
            cloud_mask = raw_clouds_image.convert("L")
            lut = [
                int(pow(i / 255.0, 1.0 / gamma) * 255.0) if i >= threshold else 0
                for i in range(256)
            ]
            cloud_mask = cloud_mask.point(lut)
            transparent_clouds_image = Image.new(
                "RGBA", raw_clouds_image.size, (0, 0, 0, 0)
            )
            white_clouds = Image.new(
                "RGBA", raw_clouds_image.size, (255, 255, 255, 255)
            )
            transparent_clouds_image.paste(white_clouds, (0, 0), mask=cloud_mask)
        logger.debug(f"Saving transparent cloud map in {self.output_path}")
        transparent_clouds_image.save(self.output_path, "PNG")

    def run(self):
        """Downloads the regional cloud layer from NASA GIBS with a baseline lookback."""
        self.exit_if_disabled()

        base_url = self.get_base_url()
        expiry_hours = self.settings.get("expiry_hours", 3)

        now_utc = datetime.now(timezone.utc)

        # Align with GFS baseline if available, but apply the lookback
        baseline = getattr(self.map_data, "shared_state", {}).get("gfs_baseline")
        if baseline:
            # We must offset from the baseline because GIBS cannot provide "today" in full yet.
            target_date = baseline["timestamp"] - timedelta(days=self.cloud_offset)
            logger.debug(
                f"Clouds syncing to baseline with a -{self.cloud_offset} day offset: {target_date.strftime('%Y-%m-%d')}"
            )
        else:
            target_date = now_utc - timedelta(days=self.cloud_offset)

        time_param = target_date.strftime("%Y-%m-%d")

        lon_min, lat_min, lon_max, lat_max = self.map_data.region.bbox
        x_min, y_min = _lonlat_to_mercator_m(lon_min, lat_min)
        x_max, y_max = _lonlat_to_mercator_m(lon_max, lat_max)
        # WMS 1.1.1 BBOX order is minx,miny,maxx,maxy (x first) — matches your existing layout
        bbox_str = f"{x_min},{y_min},{x_max},{y_max}"

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            "LAYERS": "VIIRS_SNPP_CorrectedReflectance_TrueColor",
            "FORMAT": "image/jpeg",
            "TRANSPARENT": "FALSE",
            "STYLES": "",
            "SRS": "EPSG:3857",
            "BBOX": bbox_str,
            "WIDTH": str(self.target_width),
            "HEIGHT": str(self.target_height),
            "TIME": time_param,
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        full_url = f"{base_url}?{query_string}"

        # --- Cache Logic ---
        # Only download if the file does not exist OR the file is older than the expiry limit
        if os.path.exists(self.cache_output_path):
            file_mtime = datetime.fromtimestamp(
                os.path.getmtime(self.cache_output_path), tz=timezone.utc
            )
            age = now_utc - file_mtime

            if age < timedelta(hours=expiry_hours):
                logger.info(
                    f"NASA clouds cache is fresh ({age.total_seconds() / 3600:.1f} hours old). Skipping download."
                )
                if not os.path.exists(self.output_path):
                    self.save_cache_as_transparent()
                return

        # Download raw clouds image
        try:
            os.makedirs(str(os.path.dirname(self.cache_output_path)), exist_ok=True)
            logger.info(
                f"Fetching NASA GIBS clouds for {time_param} ({self.target_width}x{self.target_height})..."
            )

            req = urllib.request.Request(
                full_url, headers={"User-Agent": "WorldMap-Cloud-Fetcher/1.0"}
            )

            with urllib.request.urlopen(req, timeout=60) as response:
                raw_clouds_image = response.read()
                with open(self.cache_output_path, "wb") as f:
                    f.write(raw_clouds_image)
            logger.debug(f"NASA cloud map downloaded into {self.cache_output_path}")
            # Save the newly cached clouds
            self.save_cache_as_transparent()

        except urllib.error.HTTPError as e:
            logger.error(f"NASA GIBS returned an error: {e.code} {e.reason}")
            if not os.path.exists(self.output_path):
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to download NASA clouds: {e}")
            if not os.path.exists(self.output_path):
                sys.exit(1)
