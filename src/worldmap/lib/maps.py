import os
import io
import requests
import logging
from PIL import Image

logger = logging.getLogger(__name__)


class NASAGIBSDownloader:
    def __init__(self):
        self.base_url = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

    def download_region_map(self, bbox, target_width, target_height, outfile, is_night=False):
        """
        Downloads and stitches regional maps to handle Date Line crossings.
        bbox: [lon_min, lat_min, lon_max, lat_max]
        """
        lon_min, lat_min, lon_max, lat_max = bbox
        success = False

        # Handle Date Line Crossing (e.g., NZ/Aus region)
        if lon_max > 180:
            logger.info(f"Correcting Date Line crossing for {outfile}...")

            # 1. Calculate spans for proportional scaling
            span_west = 180.0 - lon_min  # Distance from start to 180
            span_east = lon_max - 180.0  # Distance past 180
            total_span = span_west + span_east

            # 2. Calculate proportional pixel widths to prevent "smearing"
            width_west = int((span_west / total_span) * target_width)
            width_east = target_width - width_west

            # 3. Define the two bounding boxes
            # Tile 1: Western side (Australia/Tasman side of the line)
            bbox1 = [lon_min, lat_min, 180.0, lat_max]
            # Tile 2: Eastern side (The "overflow" into the Western Hemisphere)
            bbox2 = [-180.0, lat_min, -180.0 + span_east, lat_max]

            # 4. Fetch tiles
            img1 = self._fetch_wms_image(bbox1, is_night, width=width_west, height=target_height)
            img2 = self._fetch_wms_image(bbox2, is_night, width=width_east, height=target_height)

            if img1 and img2:
                combined = Image.new('RGB', (target_width, target_height))
                # Paste in correct geographical order: West on Left, East on Right
                combined.paste(img1, (0, 0))
                combined.paste(img2, (width_west, 0))

                os.makedirs(os.path.dirname(outfile), exist_ok=True)
                combined.save(outfile, "JPEG", quality=90)
                success = True
        else:
            # Standard single-tile download for regions NOT crossing 180
            img = self._fetch_wms_image(bbox, is_night, width=target_width, height=target_height)
            if img:
                os.makedirs(os.path.dirname(outfile), exist_ok=True)
                img.save(outfile, "JPEG", quality=90)
                success = True

        return success

    def _fetch_wms_image(self, bbox, is_night, width, height):
        layer = "VIIRS_Black_Marble" if is_night else "BlueMarble_ShadedRelief_Bathymetry"
        bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"

        params = {
            "service": "WMS",
            "request": "GetMap",
            "version": "1.1.1",
            "layers": layer,
            "styles": "",
            "format": "image/jpeg",
            "transparent": "false",
            "srs": "EPSG:4326",
            "width": str(width),
            "height": str(height),
            "bbox": bbox_str
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content))
        except Exception as e:
            logger.error(f"NASA WMS Fetch Error: {e}")
            return None
