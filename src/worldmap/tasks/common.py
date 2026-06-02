#!/usr/bin/env python3
import os
import sys
import json
import logging
import requests
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
from typing import cast, Any
from datetime import datetime, timezone, timedelta

from pathlib import Path

# Internal library import
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from PIL import Image

logger = logging.getLogger(__name__)

# These are sections which contribute to the composite layer
# image used in XPlanet rendering via the 'cloud"map' option
COMPOSITE_SECTIONS = [
    "isobars",
    "clouds",
    "precipitation",
    "wind",
    "sst",
    "currents",
    "waves",
    "temperature",
    "ozone",
    "stormwatch",
    "storms",
]
# A subset of the above list which pertain to climate layers.
# These are layers which colourise the whole active region.
CLIMATE_LAYERS = ["sst", "waves", "temperature", "ozone", "stormwatch"]


def stringify_bbox(bbox):
    """
    Converts a bbox list into a filename-safe string.
    Example: [-180.0, -90.0, 180.0, 90.0] -> "180.0W_90.0S_180.0E_90.0N"
    Or simpler: "lon-180.0_lat-90.0_lon180.0_lat90.0"
    """
    if not bbox or len(bbox) != 4:
        return "global"

    labels = ["w", "s", "e", "n"]
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
        target_height: int = 1024,
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
        return (
            self.bbox[1] <= lat <= self.bbox[3] and self.bbox[0] <= lon <= self.bbox[2]
        )

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
                logger.warning(
                    f"Region label '{region}' not found; defaulting to global"
                )
                bbox = [-180.0, -90.0, 180.0, 90.0]
                self.world_view = True
                bbox_prefix = "global_"

        # Apply aspect ratio adjustment and 180-degree safety shift
        if bbox:
            self.bbox = bbox
            target_ratio = self.target_width / self.target_height
            bbox = adjust_bbox_for_aspect_ratio(bbox, target_ratio)
            self.region_identifier = f"{bbox_prefix}_{stringify_bbox(bbox)}"
            self.centre_longitude, self.centre_latitude = get_bbox_center(bbox)


class MapData:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.region = None
        self.shared_state = {}
        self.db = Database()
        self.refresh()

    def refresh(self):
        # Acquire the target geometry
        common_settings = self.config.get_section("common")
        target_geometry = common_settings.get("target_geometry", "2048x1024")
        target_width, target_height = map(int, target_geometry.split("x"))
        # Hard-code region to None which is 'The World'
        self.region = MapRegion(None, target_width, target_height
        )


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
        self.ax = cast(
            geoaxes.GeoAxes,
            self.fig.add_axes((0, 0, 1, 1), **{"projection": projection}),
        )

        # Lock the exact view to your base map's bbox
        bbox = self.region.bbox
        self.ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        self.ax.set_aspect("auto", adjustable="box")

    def save_figure(self, output_path: str):
        self.ax.set_axis_off()
        self.ax.patch.set_alpha(0)
        self.fig.patch.set_alpha(0)

        # Atomic write/move to avoid timing issues
        base, ext = os.path.splitext(output_path)
        tmp_img = f"{base}.tmp{ext}"
        plt.savefig(tmp_img, transparent=True, bbox_inches=None, pad_inches=0)
        os.replace(tmp_img, output_path)

        plt.close(self.fig)


class Updater:
    def __init__(self, config: WorldMapConfig, section: str, map_data: MapData):
        self.config = config
        self.map_data = map_data
        self.section = section.lower()
        self.settings = config.get_section(self.section)
        self.common = config.get_section("common")
        self.workdir = self.common.get("workdir", ".")
        self.outfile = self.settings.get("outfile", "")
        self.output_path = ""
        self.enabled = self.settings.get("enabled", False)
        self.forecast_hour = max(self.common.get("forecast_hour", 1), 1)
        self.base_url = self.get_base_url()

        # Copy map data up to this class for convenience
        self.target_width = map_data.region.target_width
        self.target_height = map_data.region.target_height
        self.world_view = map_data.region.world_view
        self.map_region_identifier = map_data.region.region_identifier
        self.centre_longitude = map_data.region.centre_longitude
        self.centre_latitude = map_data.region.centre_latitude
        self.map_region_bbox = map_data.region.bbox

    def get_output_path(self):
        return str(os.path.join(self.common.get("workdir", "."), self.outfile))

    def set_output_path(self):
        self.output_path = self.get_output_path()
        file_path = Path(self.output_path)
        # Safely verify directories exist for non-image files
        if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            # Use append mode ('a') to touch/create the file if missing,
            # which keeps your existing data completely safe during re-initialization!
            with open(self.output_path, "a") as _:
                pass

    def get_output_path_if_exists(self, section=None):
        """Returns an output path for the given section, but only if the file exists"""
        outfile = self.config.get_setting(
            section if section else self.section, "outfile"
        )
        if outfile:
            output_path = str(os.path.join(self.common.get("workdir", "."), outfile))
            if os.path.exists(output_path):
                return output_path
        return None

    def get_base_url(self):
        return self.settings.get("url", "").rstrip("/")

    def remove_output_file(self):
        """Clears the output file of this updater if it exists"""
        output_path = self.get_output_path()
        if output_path and os.path.exists(output_path) and os.path.isfile(output_path):
            os.remove(output_path)

    def exit_if_disabled(self):
        if not self.enabled:
            logger.info(f"{self.section} task disabled; skipping")
            output_path = self.get_output_path()
            if output_path and os.path.dirname(output_path):
                file_path = Path(output_path)
                # create/truncate only non-image files
                if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
                    os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
                    with open(self.output_path, "w") as _:
                        pass
            sys.exit(0)

    def get_gfs_state(self):
        """
        Lazy evaluation: The first updater to call this method performs a quick network
        sync to establish the GFS datum. All subsequent updaters read from memory.
        Returns a dictionary with the synchronized date, run, and true forecast hour.
        """
        baseline = getattr(self.map_data, "shared_state", {}).get("gfs_baseline")

        # 1. ESTABLISH THE DATUM (Only runs once per map refresh)
        if not baseline:
            logger.debug(f"Section {self.section} setting up baseline")
            now = datetime.now(timezone.utc)

            gfs_base = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
            for day_offset in range(3):
                target_date = now - timedelta(days=day_offset)
                date_str = target_date.strftime("%Y%m%d")
                date_str_Y_M_D = target_date.strftime("%Y-%m-%d")

                for run in ["18", "12", "06", "00"]:
                    # We ping the .idx file because it is incredibly lightweight
                    url = f"{gfs_base}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000.idx"
                    logger.debug(f"Trying url={url}")
                    try:
                        response = requests.head(url, timeout=5)
                        if response.status_code == 200:
                            run_timestamp = target_date.replace(
                                hour=int(run), minute=0, second=0, microsecond=0
                            )
                            baseline = {
                                "date_str": date_str,
                                "date_str_Y_M_D": date_str_Y_M_D,
                                "run": run,
                                "timestamp": run_timestamp,
                            }
                            logger.debug(f"Success: run timestamp={run_timestamp}")
                            # Save globally for all other layers
                            self.map_data.shared_state["gfs_baseline"] = baseline
                            logger.debug(f"GFS Baseline Synced: {date_str} {run}Z")
                            break
                    except requests.RequestException:
                        continue
                if baseline:
                    break

            if not baseline:
                raise RuntimeError("Failed to sync GFS baseline from NOMADS.")

        # 2. CALCULATE THE DYNAMIC OFFSET (Runs for every layer)
        now = datetime.now(timezone.utc)
        user_offset_hours = self.forecast_hour

        # Calculate how old this model run is
        hours_since_run = int(
            round((now - baseline["timestamp"]).total_seconds() / 3600.0)
        )

        # Compute true internal forecast hour
        true_f_hour = max(0, hours_since_run + user_offset_hours)
        f_hour_str = f"{true_f_hour:03d}"

        # Store properties on the instance for easy access in __init__ / plot methods
        self.forecast_hour_str = f_hour_str
        self.gfs_date_str = baseline["date_str"]
        self.gfs_date_str_Y_M_D = baseline["date_str_Y_M_D"]
        self.gfs_run = baseline["run"]
        logger.debug(
            f"Section {self.section} get_gfs_state: forecast hour {f_hour_str}; date_str {self.gfs_date_str}; run {self.gfs_run}"
        )

    def get_gfs_ranges(
        self, grib_url: str, grib_targets: list[str]
    ) -> list[Any] | None:

        if not grib_targets:
            return None

        """Finds the byte ranges for both CAPE and CIN in the GFS index."""
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")

        ranges = []
        for target in grib_targets:
            for i, line in enumerate(lines):
                if target in line:
                    start_byte = int(line.split(":")[1])
                    end_byte = (
                        int(lines[i + 1].split(":")[1]) - 1
                        if i + 1 < len(lines)
                        else -1
                    )
                    ranges.append((start_byte, end_byte))
                    break

        if not ranges:
            raise RuntimeError(f"Could not find {grib_targets} in the GFS index.")

        return ranges

    def download_raw_data(
        self,
        remote_url: str,
        output_path: str,
        ranges: list[tuple[int, int]] = None,
        timeout: int = 120,
    ):
        """
        1) If ranges is left unspecified:
        If no 'ranges' are provided we just do a vanilla download, so this
        method is apt for standard non-GFS datasets.

        2) If ranges is specified:
        Download data from GFS datasets some of which allow you to specify
        byte range(s) so the whole dataset doesn't get downloaded. The ranges
        are a list of (start, end) integer tuples from which we construct
        the 'Range' header. If more than one range is provided in the 'ranges'
        list, we will do multiple downloads, one for each Range, and build
        a single file from them.
        """
        # Cater for GFS dataset which has an associated index file
        idx_path = f"{output_path}.idx"
        if os.path.exists(idx_path):
            try:
                os.remove(idx_path)
                logger.debug("Cleared stale index file.")
            except OSError:
                pass
        try:
            # Open in 'wb' mode to overwrite any old data, then we'll append the chunks
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            if ranges:
                # Range headers are to be provided, one or more
                with open(output_path, "wb") as f:
                    for start, end in ranges:
                        if end < 0:
                            headers = {"Range": f"bytes={start}-"}
                        else:
                            headers = {"Range": f"bytes={start}-{end}"}
                        r = requests.get(
                            remote_url, headers=headers, timeout=120, stream=True
                        )
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            f.write(chunk)
            else:
                # No range headers
                r = requests.get(remote_url, timeout=timeout, stream=True)
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
        except requests.RequestException as e:
            logger.error(
                f"Download of raw data for {self.section} url={remote_url} failed: {e}"
            )
            return False

        return True

    def remote_data_update(
        self, remote_url, cache_file_path, grib_targets: list[str] = None
    ) -> bool:
        """
        Check remote url for newer data, and checks existence of local cache file.
        If remote is newer, or local cache file is missing we download it.
        We return two boolean statuses: cache is present, cache_was_updated.
        The grib_targets parameter is specific to GFS remote data and allows
        headers to be specified to download particular layer of the dataset. If
        unspecified, the download is just generic, ie. the whole file.
        """
        # First ascertain cache status
        cache_exists = os.path.exists(cache_file_path)

        # Next, query the remote url
        cache_needs_update = not cache_exists
        cache_was_updated = False
        try:
            response = requests.head(remote_url, timeout=10)
            if response.status_code == 200:
                remote_mtime_str = response.headers.get("Last-Modified")
                if remote_mtime_str:
                    remote_mtime = datetime.strptime(
                        remote_mtime_str, "%a, %d %b %Y %H:%M:%S %Z"
                    ).replace(tzinfo=timezone.utc)
                    if cache_exists:
                        local_mtime = datetime.fromtimestamp(
                            os.path.getmtime(cache_file_path), tz=timezone.utc
                        )
                        if remote_mtime > local_mtime:
                            # cache exists and is out of date
                            cache_needs_update = True
                            logger.debug(
                                f"Cache file {cache_file_path} is up to date for {self.section}"
                            )

                # try to download new cache file
                if cache_needs_update:
                    logger.info(
                        f"Downloading fresh {self.section} data from {remote_url}"
                    )
                    cache_was_updated = self.download_raw_data(
                        remote_url=remote_url,
                        output_path=cache_file_path,
                        ranges=self.get_gfs_ranges(remote_url, grib_targets)
                        if grib_targets
                        else None
                        if grib_targets
                        else None,
                    )
        except requests.RequestException:
            pass

        # Return a composite status reflecting cache file availability derived
        # from the presence (or otherwise) of the cache itself and whether it
        # was updated plus two other updater statuses: whether the final output
        # path is present and whether World Map configuration has changed.
        cache_exists = os.path.exists(cache_file_path)
        return cache_exists and (
            cache_was_updated
            or not os.path.exists(self.output_path)
            or self.config.has_changed
        )

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
                    logger.debug(
                        f"Cropping image {input_path} with date line wrap for {self.map_region_identifier}"
                    )
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
                    w_a = int(
                        ((180 - lon_min) / (lon_max - lon_min)) * self.target_width
                    )
                    w_b = self.target_width - w_a

                    regional_image = Image.new(
                        "RGB", (self.target_width, self.target_height)
                    )
                    regional_image.paste(
                        tile_a.resize(
                            (w_a, self.target_height), Image.Resampling.LANCZOS
                        ),
                        (0, 0),
                    )
                    regional_image.paste(
                        tile_b.resize(
                            (w_b, self.target_height), Image.Resampling.LANCZOS
                        ),
                        (w_a, 0),
                    )
                else:
                    # Standard linear crop
                    x1, y1 = get_px(lon_min, lat_max)
                    x2, y2 = get_px(lon_max, lat_min)
                    regional_image = img.crop((x1, y1, x2, y2)).resize(
                        (self.target_width, self.target_height),
                        Image.Resampling.LANCZOS,
                    )

                return regional_image
                # regional_image.save(new_image_path, "JPEG", quality=90)
        except Exception as e:
            logger.error(f"Failed to crop to regional image: {e}")

        return None

    def climate_layer_is_active(self):
        """Return True if at least one climate layer is enabled"""
        for layer in CLIMATE_LAYERS:
            if self.config.section_enabled(layer):
                return True
        return False

    def create_plot(self):
        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        projection = ccrs.PlateCarree()
        ax = cast(
            geoaxes.GeoAxes, fig.add_axes((0, 0, 1, 1), **{"projection": projection})
        )

        # Lock the exact view to your base map's bbox
        bbox = self.map_region_bbox
        ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        ax.set_aspect("auto", adjustable="box")

        return fig, ax
