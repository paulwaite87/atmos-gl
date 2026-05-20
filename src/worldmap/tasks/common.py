#!/usr/bin/env python3
import os
import sys
import json
import logging
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
from typing import cast

from pathlib import Path

# Internal library import
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from PIL import Image


logger = logging.getLogger(__name__)

# These are sections which contribute to the composite layer
# image used in XPlanet rendering via the 'cloud"map' option
COMPOSITE_SECTIONS = [
    "sst",
    "temperature",
    "currents",
    "waves",
    "clouds",
    "clouds_nasa",
    "precipitation",
    "isobars",
    "wind",
    "storms"
]

def listify(text: str) -> list:
    """Convert a comma-separated string into a list of strings"""
    if not text or text.strip() == "":
        return []
    return [item.strip() for item in text.split(',')]

def stringify_bbox(bbox):
    """
    Converts a bbox list into a filename-safe string.
    Example: [-180.0, -90.0, 180.0, 90.0] -> "180.0W_90.0S_180.0E_90.0N"
    Or simpler: "lon-180.0_lat-90.0_lon180.0_lat90.0"
    """
    if not bbox or len(bbox) != 4:
        return "global"

    labels = ['w', 's', 'e', 'n']
    return "_".join(f"{labels[i]}{abs(bbox[i]):.1f}" for i in range(4))

def adjust_bbox_for_aspect_ratio(bbox, target_ratio=2.0):
    """
    Ensures the bbox matches the target aspect ratio and stays <= 180.0 longitude.
    Shifts the entire window west if the expansion hits the Date Line.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    delta_lon = lon_max - lon_min
    delta_lat = lat_max - lat_min

    if delta_lat == 0:
        return bbox

    current_ratio = delta_lon / delta_lat

    if current_ratio < target_ratio:
        target_lon_span = delta_lat * target_ratio
        padding = (target_lon_span - delta_lon) / 2
        lon_min -= padding
        lon_max += padding
    elif current_ratio > target_ratio:
        target_lat_span = delta_lon / target_ratio
        padding = (target_lat_span - delta_lat) / 2
        lat_min -= padding
        lat_max += padding

    # Latitude Safety Cap
    if lat_max > 90:
        shift = lat_max - 90
        lat_max = 90
        lat_min -= shift
    if lat_min < -90:
        shift = -90 - lat_min
        lat_min = -90
        lat_max += shift

    # Longitude Safety Cap (The 180-degree Shift)
    # If the box goes past 180, we slide the whole window west.
    if lon_max > 180.0:
        shift = lon_max - 180.0
        lon_max = 180.0
        lon_min -= shift

    if lon_min < -180.0:
        shift = -180.0 - lon_min
        lon_min = -180.0
        lon_max += shift

    return [lon_min, lat_min, lon_max, lat_max]

def get_bbox_center(bbox):
    """
    Returns the center (longitude, latitude) for a given bbox.
    bbox: [lon_min, lat_min, lon_max, lat_max]
    """
    lon_min, lat_min, lon_max, lat_max = bbox

    # Center Latitude is a straight average
    center_lat = (lat_min + lat_max) / 2

    # Center Longitude
    # Handle the Date Line: if the span is negative or crosses 180
    delta_lon = lon_max - lon_min
    center_lon = lon_min + (delta_lon / 2)

    # Normalize longitude to stay within [-180, 180]
    if center_lon > 180:
        center_lon -= 360
    elif center_lon < -180:
        center_lon += 360

    return center_lon, center_lat

class MapRegion:
    def __init__(
            self,
            region: str | list[float] | None = None,
            target_width: int = 2048,
            target_height: int = 1024
    ):
        self.region = region
        self.region_identifier = "region"
        self.target_width = target_width
        self.target_height = target_height
        self.bbox = None
        self.world_view = False
        self.centre_latitude = 0.0
        self.centre_longitude = 0.0
        self.set_map_region_data(region)

    def is_in_region(self, lat: float, lon: float):
        return (self.bbox[1] <= lat <= self.bbox[3] and
                self.bbox[0] <= lon <= self.bbox[2])

    def set_map_region_data(self, region: str | list[float] | None):
        bbox = None
        bbox_prefix = "region_"
        self.world_view = False

        # Handle explicit 'falsy' regions (None, empty string)
        if not region:
            bbox = [-180.0, -90.0, 180.0, 90.0]
            self.world_view = True
            bbox_prefix = "bbox_"

        elif str(region).startswith("["):
            try:
                data = json.loads(str(region))
                if isinstance(data, list) and not data:
                    bbox = [-180.0, -90.0, 180.0, 90.0]
                    self.world_view = True
                    bbox_prefix = "global_"
                else:
                    bbox = [float(x) for x in data]
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.error(f"Invalid BBox format for '{region}': {e}")

        else:
            # Database lookup
            db = Database()
            bbox_row = db.get_region_definition(str(region))
            if bbox_row:
                bbox = [val for _, val in bbox_row.items()]
                bbox_prefix = f"{bbox_prefix}_{region}"
            else:
                logger.warning(f"Region label '{region}' not found; defaulting to global")
                bbox = [-180.0, -90.0, 180.0, 90.0]
                self.world_view = True
                bbox_prefix = "global_"

        # Apply aspect ratio adjustment and 180-degree safety shift
        if bbox:
            # lon_min is index 0, lon_max is index 2
            if bbox[2] > 180.0:
                overflow = bbox[2] - 180.0
                bbox[2] = 180.0  # Cap East at 180
                bbox[0] = bbox[0] - overflow  # Shift West by the same amount

            self.bbox = bbox
            target_ratio = self.target_width / self.target_height
            bbox = adjust_bbox_for_aspect_ratio(bbox, target_ratio)
            self.region_identifier = f"{bbox_prefix}_{stringify_bbox(bbox)}"
            self.centre_longitude, self.centre_latitude = get_bbox_center(bbox)


class MapData:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.region = None
        self.db = Database()
        self.refresh()

    def refresh(self):
        # Acquire the target geometry
        common_settings = self.config.get_section("common")
        target_geometry = common_settings.get("target_geometry", fallback="2048x1024")
        target_width, target_height = map(int, target_geometry.split('x'))
        self.region = MapRegion(
            self.config.get_setting("common", "region"),
            target_width,
            target_height
        )

        # Override longitude if we are viewing a global region
        user_longitude = self.config.get_setting("xplanet", "longitude")
        if user_longitude and self.region.world_view:
            self.region.centre_longitude = user_longitude


class Plot:
    def __init__(self, region: MapRegion):
        self.region = region
        self.fig = None
        self.ax = None

    def get_figure(self):
        plot_target_width = float(self.region.target_width) / 100
        plot_target_height = float(self.region.target_height) / 100

        self.fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        projection = ccrs.PlateCarree()
        self.ax = cast(geoaxes.GeoAxes, self.fig.add_axes((0, 0, 1, 1), **{'projection': projection}))

        # Lock the exact view to your base map's bbox
        bbox = self.region.bbox
        self.ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        self.ax.set_aspect('auto', adjustable='box')

    def save_figure(self, output_path: str):
        self.ax.set_axis_off()
        self.ax.patch.set_alpha(0)
        self.fig.patch.set_alpha(0)

        plt.savefig(output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(self.fig)

class Updater:
    def __init__(self, config: WorldMapConfig, section: str, map_data: MapData):
        self.config = config
        self.map_data = map_data
        self.section = section.lower()
        self.settings = config.get_section(self.section)
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")
        self.outfile = self.settings.get("outfile", fallback="")
        self.output_path = ""
        self.enabled = self.settings.getboolean("enabled", False)

        # Copy map data up to this class for convenience
        self.target_width = map_data.region.target_width
        self.target_height = map_data.region.target_height
        self.world_view = map_data.region.world_view
        self.map_region_identifier = map_data.region.region_identifier
        self.centre_longitude = map_data.region.centre_longitude
        self.centre_latitude = map_data.region.centre_latitude
        self.map_region_bbox = map_data.region.bbox

    def exit_if_disabled(self):
        if not self.enabled:
            logger.info(f"{self.section} task disabled; skipping.")
            sys.exit(0)

    def get_output_path(self):
        return str(os.path.join(self.common.get("workdir", "."), self.outfile))

    def set_output_path(self):
        self.output_path = self.get_output_path()
        file_path = Path(self.output_path)
        # Initialise non-image files
        if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            with open(self.output_path, "w") as _:
                pass
        else:
            pass

    def get_output_path_if_exists(self, section=None):
        """Returns an output path for the given section, but only if the file exists"""
        outfile = self.config.get_setting(section if section else self.section, "outfile")
        if outfile:
            output_path = str(os.path.join(self.common.get("workdir", "."), outfile))
            if os.path.exists(output_path):
                return output_path
        return None

    def remove_output_file(self):
        """Clears the output file of this updater if it exists"""
        output_path = self.get_output_path()
        if output_path and os.path.exists(output_path) and os.path.isfile(output_path):
            os.remove(output_path)

    def get_regional_image(self, input_path: str = None) -> Image.Image | None:
        """Returns an image object which is cropped to the active region"""
        # Default to replacing updater's output image
        if not input_path:
            input_path = self.get_output_path()

        try:
            with Image.open(input_path) as img:
                region_bbox = self.map_region_bbox

                # do nothing if no region
                if not region_bbox:
                    return img

                src_w, src_h = img.size
                lon_min, lat_min, lon_max, lat_max = region_bbox

                def get_px(lon, lat):
                    """Converts lat/lon to pixel coordinates on the global source map."""
                    # Normalize -180...180 to 0...1 (180 becomes 1.0, not 0)
                    x_pct = (lon + 180) / 360
                    # Clamp to prevent edge-case pixel overflows
                    x = max(0, min(src_w - 1, int(x_pct * src_w)))

                    # Latitude 90 (North) is Y=0, -90 (South) is Y=src_h
                    y_pct = (90 - lat) / 180
                    y = max(0, min(src_h - 1, int(y_pct * src_h)))
                    return x, y

                if lon_max > 180:
                    logger.debug(f"Cropping image {input_path} with date line wrap for {self.map_region_identifier}")
                    # TILE A: The "Western" part (e.g., 112 to 180)
                    ax1, ay1 = get_px(lon_min, lat_max)
                    ax2, ay2 = get_px(180, lat_min)
                    # PIL.crop uses (left, top, right, bottom)
                    tile_a = img.crop((ax1, ay1, ax2, ay2))

                    # TILE B: The "Eastern" part (e.g., -180 to -178.9)
                    bx1, by1 = get_px(-180, lat_max)
                    bx2, by2 = get_px(lon_max - 360, lat_min)
                    tile_b = img.crop((bx1, by1, bx2, by2))

                    # Calculate the seam point proportionally
                    w_a = int(((180 - lon_min) / (lon_max - lon_min)) * self.target_width)
                    w_b = self.target_width - w_a

                    regional_image = Image.new('RGB', (self.target_width, self.target_height))
                    regional_image.paste(tile_a.resize((w_a, self.target_height), Image.Resampling.LANCZOS), (0, 0))
                    regional_image.paste(tile_b.resize((w_b, self.target_height), Image.Resampling.LANCZOS), (w_a, 0))
                else:
                    # Standard linear crop
                    x1, y1 = get_px(lon_min, lat_max)
                    x2, y2 = get_px(lon_max, lat_min)
                    regional_image = img.crop((x1, y1, x2, y2)).resize((self.target_width, self.target_height), Image.Resampling.LANCZOS)

                return regional_image
                #regional_image.save(new_image_path, "JPEG", quality=90)
        except Exception as e:
            logger.error(f"Failed to crop to regional image: {e}")

        return None

    def create_plot(self):
        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        projection = ccrs.PlateCarree()
        ax = cast(geoaxes.GeoAxes, fig.add_axes((0, 0, 1, 1), **{'projection': projection}))

        # Lock the exact view to your base map's bbox
        bbox = self.map_region_bbox
        ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        ax.set_aspect('auto', adjustable='box')

        return fig, ax
