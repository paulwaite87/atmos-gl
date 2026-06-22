#!/usr/bin/env python3
import os
import sys
import json
import logging
import requests
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
import numpy as np
from PIL import Image
from typing import cast, Any
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Internal library import
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from worldmap.lib import fieldstore

logger = logging.getLogger(__name__)

WEB_MERCATOR = ccrs.Mercator.GOOGLE  # EPSG:3857
MERCATOR_LAT_LIMIT = 85.0511  # NOTE: just *inside* GOOGLE's 85.0511288 max


def encode_frames(frames, output_path, vmin, vmax, transform=None, bits=16):
    """
    Stack N scalar fields vertically into a single RGBA PNG, for upload as a
    WebGL2 2D-array texture (one array layer per frame, frame 0 on top).

    bits=16 (default): R = high byte, G = low byte of a 16-bit normalised value
      (65535 levels), B=0, A = mask. Decode on the GPU: norm = (R*256 + G)/65535.
      65535 levels eliminates the visible value-stepping that 8-bit (256 levels)
      causes — most obvious on thin contour lines (isobars), but it also removes
      faint banding in colour ramps. This is the default for all raster layers.
    bits=8: R = normalised value (0..1 -> 0..255), G=B=0, A = mask. Legacy/compact.

    transform:
      None    -> linear normalisation (m - vmin) / (vmax - vmin)
      'sqrt'  -> sqrt of the linear norm; gives the low end far more precision
                 (e.g. precipitation). Combines with 16-bit for even finer low end.
    Decode on the GPU as: value = norm (then square it for 'sqrt' layers).
    """
    span = float(vmax - vmin)
    slabs = []
    shape0 = None
    for m in frames:
        m = np.asarray(m, dtype=np.float32)
        if shape0 is None:
            shape0 = m.shape
        elif m.shape != shape0:
            raise ValueError(f"Frame shape mismatch: {m.shape} vs {shape0}")
        norm = np.clip((m - vmin) / span, 0.0, 1.0)
        if transform == "sqrt":
            norm = np.sqrt(norm)
        norm = np.nan_to_num(norm, nan=0.0)  # NaN -> 0 (masked out via alpha)
        a = np.where(np.isnan(m), 0, 255).astype(np.uint8)
        if bits == 16:
            q = np.clip(np.round(norm * 65535.0), 0, 65535).astype(np.uint32)
            hi = (q >> 8).astype(np.uint8)  # R = high byte
            lo = (q & 0xFF).astype(np.uint8)  # G = low byte
            z = np.zeros_like(hi)
            slabs.append(np.dstack((hi, lo, z, a)))
        else:
            r = (norm * 255.0).astype(np.uint8)
            z = np.zeros_like(r)
            slabs.append(np.dstack((r, z, z, a)))
    filmstrip = np.vstack(slabs)  # (N*H, W, 4)
    Image.fromarray(filmstrip, mode="RGBA").save(output_path, format="PNG")
    logger.debug(
        f"Saved {len(frames)}-frame data texture ({bits}-bit) to {output_path} {filmstrip.shape}"
    )
    return True


def encode_uv(u, v, output_path, vmax, lat=None):
    """
    Encode a global vector field (U=east, V=north, in m/s) into a single RGBA PNG
    for a GPU particle layer:  R = (U + vmax) / (2*vmax),  G = (V + vmax) / (2*vmax),
    B = 0,  A = 255 (0 where NaN).  Row 0 = north, lon -180..180.
    Decode on the GPU as:  component = channel * (2*vmax) - vmax.
    vmax clips extremes; pick it a little above the strongest winds you care about.

    The particle shader's toMerc() maps the top texture row to +90 lat and treats G as
    the true northward component, so the texture MUST be north-at-top. cfgrib does not
    guarantee a row order (it can hand back latitude ascending = south-first depending on
    the GRIB), and unpack/_standardize_lon only normalises longitude. If south-first rows
    reach here, the field is encoded vertically mirrored: every particle samples the wrong
    hemisphere AND the (un-negated) V is inconsistent with the flipped geometry, which turns
    rotation into divergence — highs render as radial outflow instead of circulating.
    Passing `lat` lets this self-orient: if lat is ascending we flip the rows to north-first
    so the output is correct regardless of what cfgrib produced. lat and u/v are guaranteed
    consistent here (they come from the same fieldstore .npz).
    """
    u = np.asarray(u, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    if u.shape != v.shape:
        raise ValueError(f"U/V shape mismatch: {u.shape} vs {v.shape}")
    # Guarantee north-at-top. If latitude runs south->north (ascending), flip the rows.
    if lat is not None:
        lat = np.asarray(lat)
        if lat.ndim == 1 and lat.size >= 2 and float(lat[0]) < float(lat[-1]):
            u = u[::-1]
            v = v[::-1]
    span = 2.0 * float(vmax)
    mask = np.isnan(u) | np.isnan(v)
    ru = np.clip((np.nan_to_num(u) + vmax) / span, 0.0, 1.0)
    rv = np.clip((np.nan_to_num(v) + vmax) / span, 0.0, 1.0)
    r = (ru * 255.0).astype(np.uint8)
    g = (rv * 255.0).astype(np.uint8)
    z = np.zeros_like(r)
    a = np.where(mask, 0, 255).astype(np.uint8)
    img = np.dstack((r, g, z, a))
    Image.fromarray(img, mode="RGBA").save(output_path, format="PNG")
    logger.debug(f"Saved wind vector texture to {output_path} {img.shape}")
    return True


def smooth_flow_direction(u, v, radius):
    """Direction-coherence smoothing for the particle ADVECTION field.

    Coarse 0.25 deg GFS renders a shear (two flows meeting at an angle) as an abrupt
    1-cell direction flip. When the frontend interpolates the raw Cartesian U/V across
    that seam, the opposing components partially CANCEL, so the interpolated vector has a
    collapsed magnitude right at the boundary — a low-speed 'dead zone'. Particles entering
    it decelerate and dwell (piling into bright lines) or stall, so the two regions read as
    independent blocks with a hard seam instead of one flow curving into the other.

    This rewrites each cell as  SPEED * smoothed-unit-DIRECTION:
      - SPEED (the per-cell magnitude) is kept EXACTLY, so the wind-speed colours / fine
        detail are unchanged (colour depends only on magnitude).
      - DIRECTION is averaged as unit vectors over a ~`radius`-cell Gaussian neighbourhood
        (unit-vector form takes the shortest rotation and has no 360-degree wrap problem),
        turning the 1-cell flip into a gradual, coherent multi-cell turn.
    The seam no longer collapses in magnitude, so particles ride a smooth curve through it
    at sustained speed — the windy.com look — without removing the calm/age recycling that
    keeps genuinely dead zones from clumping.

    radius is in grid cells (~0.25 deg each); 0 disables. Longitude wraps, latitude clamps.
    """
    if radius is None or radius <= 0:
        return u, v
    from scipy.ndimage import gaussian_filter
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    spd = np.hypot(u, v)
    eps = 1e-6
    ux = np.nan_to_num(u / (spd + eps))
    uy = np.nan_to_num(v / (spd + eps))
    # Smooth the unit-direction field. axis 0 = latitude (clamp at the poles), axis 1 =
    # longitude (wrap around the globe so the dateline has no seam).
    sux = gaussian_filter(ux, radius, mode=["nearest", "wrap"])
    suy = gaussian_filter(uy, radius, mode=["nearest", "wrap"])
    norm = np.hypot(sux, suy) + eps
    return spd * sux / norm, spd * suy / norm


def _opaque_cmap(cmap, n=256):
    """Return an opaque copy of a colormap (alpha forced to 1.0)."""
    import numpy as np
    import matplotlib.colors as mcolors

    colors = cmap(np.linspace(0, 1, n))  # (n, 4) RGBA
    colors[:, 3] = 1.0
    return mcolors.ListedColormap(colors)


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


def encode_data_texture(matrix_t0, matrix_t1, output_path, vmin, vmax):
    """
    Encodes two timesteps of scalar data (e.g., pressure) into the Red and Green
    channels of a PNG image for WebGL shader interpolation.

    Args:
        matrix_t0: 2D numpy array of data at Hour 0.
        matrix_t1: 2D numpy array of data at Hour 6 (or next step).
        output_path: Destination filepath (e.g., 'data/isobars_data.png').
        vmin: Minimum expected physical value (maps to pixel value 0).
        vmax: Maximum expected physical value (maps to pixel value 255).
    """
    # 1. Ensure matrices are float arrays and dimensions match
    matrix_t0 = np.asarray(matrix_t0, dtype=np.float32)
    matrix_t1 = np.asarray(matrix_t1, dtype=np.float32)

    if matrix_t0.shape != matrix_t1.shape:
        raise ValueError(
            f"Matrix shape mismatch: {matrix_t0.shape} vs {matrix_t1.shape}"
        )

    height, width = matrix_t0.shape

    # 2. Normalize data mathematically to a 0.0 - 1.0 range
    # Example: If vmin=950 and vmax=1050, a pressure of 1000 becomes 0.5.
    norm_t0 = (matrix_t0 - vmin) / (vmax - vmin)
    norm_t1 = (matrix_t1 - vmin) / (vmax - vmin)

    # 3. Clip out-of-bounds values to strictly stay within 0.0 and 1.0
    # This prevents extreme weather anomalies from overflowing the 8-bit integer
    norm_t0 = np.clip(norm_t0, 0.0, 1.0)
    norm_t1 = np.clip(norm_t1, 0.0, 1.0)

    # 4. Scale to 0 - 255 and convert to 8-bit unsigned integers (pixels)
    r_channel = (norm_t0 * 255.0).astype(np.uint8)
    g_channel = (norm_t1 * 255.0).astype(np.uint8)

    # 5. Create Blue and Alpha channels
    # Blue is unused for now (set to 0)
    b_channel = np.zeros((height, width), dtype=np.uint8)
    # Alpha defaults to 255 (fully visible)
    a_channel = np.full((height, width), 255, dtype=np.uint8)

    # 6. Handle Missing Data (NaNs)
    # If the interpolator produced NaNs (e.g., off the edge of the map),
    # we set the Alpha channel to 0 so the WebGL shader knows to ignore it.
    nan_mask = np.isnan(matrix_t0) | np.isnan(matrix_t1)
    a_channel[nan_mask] = 0

    # 7. Stack channels into a single (Height, Width, 4) RGBA array
    rgba_array = np.dstack((r_channel, g_channel, b_channel, a_channel))

    # 8. Generate and save the PNG losslessly
    # We must use PNG because JPEG compression alters pixel colors,
    # which would corrupt our physical data.
    img = Image.fromarray(rgba_array, mode="RGBA")
    img.save(output_path, format="PNG")

    return True


class MapRegion:
    def __init__(
        self,
        target_geometry: str | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        region: str | list[float] | None = None,
    ):
        self.region = region
        self.region_identifier = "region"
        self.target_width = target_width
        self.target_height = target_height
        self.region_geometry = target_geometry
        # Solve inter-dependency of these dimensions; explicit dims get
        # priority over the composite geometry string
        if isinstance(target_width, int) and isinstance(target_height, int):
            self.target_geometry = f"{self.target_width}x{self.target_height}"
        elif target_geometry and "x" in target_geometry:
            self.target_width = int(target_geometry.split("x")[0])
            self.target_height = int(target_geometry.split("x")[1])
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
        self.region = MapRegion(target_geometry=target_geometry)


class Plot:
    def __init__(self, region: MapRegion, projection=WEB_MERCATOR):
        self.region = region
        self.projection = projection
        self.fig = None
        self.ax = None

    def get_figure(self):
        plot_target_width = float(self.region.target_width) / 100
        plot_target_height = float(self.region.target_height) / 100
        self.fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)
        self.ax = cast(
            geoaxes.GeoAxes, self.fig.add_axes((0, 0, 1, 1), projection=self.projection)
        )
        bbox = self.region.bbox
        lat_lo = max(bbox[1], -MERCATOR_LAT_LIMIT)
        lat_hi = min(bbox[3], MERCATOR_LAT_LIMIT)
        # extent is ALWAYS given in lon/lat degrees, regardless of axes projection
        self.ax.set_extent([bbox[0], bbox[2], lat_lo, lat_hi], crs=ccrs.PlateCarree())
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
        self.animation = config.get_section("animation")
        self.workdir = self.common.get("workdir", ".")
        self.outfile = self.settings.get("outfile", "")
        self.output_path = None
        self.enabled = self.settings.get("enabled", False)
        # This is the starting hour (offset) for all renders. It used to be a
        # configurable setting, but since we moved to creating all renders for
        # each hour, and allow the user to play through them, this is not
        # useful to them. We hard-code it to zero here for now.
        self.forecast_hour = 0
        # Per-hour output suffixes a COMPLETE render produces for this layer, relative
        # to the per-hour base (e.g. "isobars_f004"). should_plot_for_hour treats an
        # hour as stale if ANY of these is missing, so deleting (say) a _data.png
        # forces a re-render even when the static .png still exists. Subclasses
        # override this to match what their plot() actually writes. Default: a single
        # static PNG (legacy/plain layers).
        self.per_hour_outputs = [".png"]

        # Copy map data up to this class for convenience
        self.target_width = map_data.region.target_width
        self.target_height = map_data.region.target_height
        self.world_view = map_data.region.world_view
        self.map_region_identifier = map_data.region.region_identifier
        self.centre_longitude = map_data.region.centre_longitude
        self.centre_latitude = map_data.region.centre_latitude
        self.map_region_bbox = map_data.region.bbox

        # Always set these, which can be over-ridden later if required.
        # If the updater doesn't have an outfile defined, this does nothing.
        self.set_output_path()
        self.base_url = self.get_base_url()

    def get_output_path(self) -> str | None:
        return (
            str(os.path.join(self.common.get("workdir", "."), self.outfile))
            if self.outfile
            else None
        )

    def set_output_path(self):
        self.output_path = self.get_output_path()
        if self.output_path:
            file_path = Path(self.output_path)
            # Safely verify directories exist for non-image files
            if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
                os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
                # Use append mode ('a') to touch/create the file if missing
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

    def cache_path(self, filename: str) -> str:
        """Path for a downloadable cache file under the data dir.

        Every cache file carries a uniform '<section>_cache_' prefix. This lets the
        housekeeper find and expire caches by a single marker (no per-layer pattern
        lists to maintain), and makes live render outputs - which never carry the
        prefix - safe from deletion by construction rather than by a guard list.
        """
        return str(
            os.path.join(self.workdir, "data", f"{self.section}_cache_{filename}")
        )

    def get_base_url(self):
        return self.settings.get("url", "").rstrip("/")

    def remove_output_file(self):
        """Clears the output file of this updater if it exists"""
        output_path = self.get_output_path()
        if output_path and os.path.exists(output_path) and os.path.isfile(output_path):
            os.remove(output_path)

    def get_db_field(self, product_name: str) -> dict | None:
        """Fetch a pre-processed field set from the fieldstore (catalog + file).

        Requires that get_gfs_state() has been called first (so run_date_str, run_id,
        forecast_hour_str are set). Returns the field dict with keys:
          lat, lon, values, values2, u, v, valid_time
        or None if the field doesn't exist (collector hasn't run yet, or the product
        failed to unpack).
        """
        if not hasattr(self, "run_date_str") or not hasattr(self, "run_id"):
            logger.warning(f"{self.section}: get_db_field called before get_gfs_state")
            return None
        fhour = int(self.forecast_hour_str)
        try:
            fs = fieldstore.get_store(self.workdir)
            field = fs.get_field(self.run_date_str, self.run_id, fhour, product_name)
            if field:
                logger.debug(
                    f"{self.section}: loaded {product_name} from fieldstore "
                    f"({self.run_date_str}/{self.run_id}/f{fhour:03d})"
                )
            return field
        except Exception as e:
            logger.error(f"{self.section}: get_db_field({product_name}) failed: {e}")
            return None

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

    def get_db_field_at_hour(self, product_name: str, fhour: int) -> dict | None:
        """Fetch a pre-processed field from the fieldstore for a specific forecast hour.
        Used by animation frame loops and other multi-hour operations.
        Args:
            product_name: The product name (e.g., "precipitation", "wind")
            fhour: The forecast hour (e.g., 3, 6, 9, ...)
        Returns:
            Field dict {lat, lon, values, values2, u, v, valid_time} or None
        """
        if not hasattr(self, "run_date_str") or not hasattr(self, "run_id"):
            logger.debug(
                f"get_db_field_at_hour({product_name}, f{fhour:03d}): GFS state not set"
            )
            return None
        try:
            fs = fieldstore.get_store(self.workdir)
            return fs.get_field(
                self.run_date_str, self.run_id, int(fhour), product_name
            )
        except Exception as e:
            logger.debug(
                f"get_db_field_at_hour({product_name}, f{fhour:03d}) failed: {e}"
            )
            return None

    def should_plot_for_hour(self, product_name: str, fhour: int | str = None) -> bool:
        """Check if a per-hour output needs updating.

        Returns True if:
          - The output file doesn't exist, OR
          - The field's valid_time is newer than the output file's mtime

        Returns False if the file is already fresh. This prevents re-plotting
        when data hasn't changed. Uses the catalog metadata only (no array load).
        """
        if fhour is None:
            fhour = int(self.forecast_hour_str)
        else:
            fhour = int(fhour)

        output_path = self.get_output_path_for_hour(fhour)
        base, ext = os.path.splitext(output_path)

        # A complete render produces every suffix in self.per_hour_outputs. If ANY is
        # missing, re-plot to fill the gap (this is what makes "delete a _data.png to
        # force regeneration" work even when the static .png is still present).
        required_paths = []
        for suffix in self.per_hour_outputs or [".png"]:
            # ".png" -> the static per-hour file; "_data.png"/"_labels.geojson" -> base+suffix.
            required_paths.append(output_path if suffix == ext else f"{base}{suffix}")
        missing = [p for p in required_paths if not os.path.exists(p)]
        if missing:
            return True

        # All outputs exist — check freshness against when the data was written.
        # Use the static PNG's mtime as the reference (oldest-equivalent; all outputs
        # are written together in one plot() call).
        try:
            fs = fieldstore.get_store(self.workdir)
            meta = fs.get_field_meta(
                self.run_date_str, self.run_id, fhour, product_name
            )

            if not meta or meta.get("updated_at") is None:
                # No data catalogued, don't plot (data isn't ready yet)
                return False

            # Get file's mtime and compare to when the DATA ROW was last written.
            # NOTE: use updated_at (when the field was stored), NOT valid_time (the
            # forecast's validity time, which is usually in the future and would make
            # every hour look "newer" than its PNG, forcing a re-plot every cycle).
            file_mtime = min(os.path.getmtime(p) for p in required_paths)
            file_dt = datetime.fromtimestamp(file_mtime, tz=timezone.utc)

            field_updated = meta.get("updated_at")
            if field_updated is None:
                return False

            # Ensure both are tz-aware for comparison
            if field_updated.tzinfo is None:
                field_updated = field_updated.replace(tzinfo=timezone.utc)

            # Plot if data is newer (with a 1-second tolerance for clock skew)
            return (field_updated - file_dt).total_seconds() > 1

        except Exception as e:
            logger.debug(
                f"should_plot_for_hour({product_name}, f{fhour:03d}) check failed: {e}"
            )
            # On error, be conservative — don't plot (file is probably fine)
            return False

    def get_output_path_for_hour(self, fhour: int | str = None) -> str:
        """Return a per-hour output path for caching renders.

        If fhour is None, uses self.forecast_hour_str. The path is:
          {base_path}_f{fhour:03d}.png

        Example: "/path/to/precipitation_f003.png"
        """
        if fhour is None:
            fhour = int(self.forecast_hour_str)
        else:
            fhour = int(fhour)

        base, ext = os.path.splitext(self.output_path)
        return f"{base}_f{fhour:03d}{ext}"

    def publish_current_hour(self, fhour: int | str = None):
        """Publish the current forecast hour's render to the STABLE base filename.

        The backend caches per-hour ({base}_fNNN.png and {base}_fNNN_data.png), but the
        frontend fetches the run-agnostic base names ({base}.png and {base}_data.png) —
        it has no way to know which forecast hour is valid "now". This copies the
        per-hour outputs to those base names so the frontend always sees the latest hour.

        Copies whichever of the two artifacts exist (static PNG and/or _data.png texture),
        using atomic replace so the frontend never reads a half-written file.
        """
        if fhour is None:
            fhour = int(self.forecast_hour_str)
        else:
            fhour = int(fhour)

        base, ext = os.path.splitext(self.output_path)
        per_hour = f"{base}_f{fhour:03d}{ext}"

        pairs = [
            (per_hour, self.output_path),  # static raster
            (
                f"{base}_f{fhour:03d}_data.png",
                f"{base}_data.png",
            ),  # multi-frame texture
        ]
        import shutil

        for src, dst in pairs:
            if not os.path.exists(src):
                continue
            try:
                tmp = f"{dst}.tmp"
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                logger.debug(
                    f"{self.section}: published {os.path.basename(src)} -> {os.path.basename(dst)}"
                )
            except Exception as e:
                logger.warning(f"{self.section}: failed to publish {src} -> {dst}: {e}")

    def render_all_hours(self, product_name, plot_fn, field_ready):
        """Gap-filling per-hour render loop.

        The scrubber needs a rendered PNG for every forecast hour that has data, not
        just the current one. This loops over the hours present in the catalog for
        this run and plots any whose output is missing or stale (should_plot_for_hour
        decides per hour). Hours already rendered and fresh are skipped cheaply, so
        steady state is N metadata checks and zero re-renders; only newly-arrived or
        deleted hours actually plot.

        Args:
            product_name: catalog product key (e.g. "isobars").
            plot_fn: callable(field) that renders + writes the per-hour outputs for
                     the hour currently set in self.forecast_hour_str.
            field_ready: callable(field) -> bool; whether the fetched field has the
                     data this layer needs (e.g. values is not None; u/v for wind).

        Returns the number of hours actually (re)plotted.
        """
        try:
            from worldmap.lib.db import Database

            db = Database()
            hours = db.get_product_hours(self.run_date_str, self.run_id, product_name)
        except Exception as e:
            logger.warning(f"{self.section}: could not list hours: {e}")
            hours = []

        if not hours:
            logger.info(
                f"{self.section}: no hours in catalog yet (collector may not have run)."
            )
            return 0

        # Preserve the task's notion of 'current hour' so we can restore it after.
        saved_fhour = getattr(self, "forecast_hour_str", None)
        plotted = 0
        try:
            for fh in hours:
                # Point the per-hour-aware helpers (get_output_path_for_hour, plot's
                # save paths, should_plot_for_hour, publish) at THIS hour.
                self.forecast_hour_str = f"{int(fh):03d}"
                if not self.should_plot_for_hour(product_name, fh):
                    continue
                field = self.get_db_field_at_hour(product_name, fh)
                if not field or not field_ready(field):
                    continue
                try:
                    plot_fn(field)
                    plotted += 1
                except Exception as e:
                    logger.warning(f"{self.section}: plot f{int(fh):03d} failed: {e}")
        finally:
            if saved_fhour is not None:
                self.forecast_hour_str = saved_fhour

        if plotted:
            logger.info(
                f"{self.section}: rendered {plotted} hour(s) "
                f"({len(hours)} available, {len(hours) - plotted} already fresh)."
            )
        else:
            logger.debug(
                f"{self.section}: all {len(hours)} hour(s) fresh; nothing to render."
            )

        # Keep the stable base-name static current (for forecast_stepping=off / fallback).
        self.publish_current_hour()
        return plotted

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
        self.run_date_str = baseline["date_str"]
        self.run_date_str_Y_M_D = baseline["date_str_Y_M_D"]
        self.run_id = baseline["run"]
        logger.debug(
            f"Section {self.section} get_gfs_state: forecast hour {f_hour_str}; date_str {self.run_date_str}; run {self.run_id}"
        )

    def get_rtofs_state(self):
        """RTOFS (ocean) analogue of get_gfs_state, for currents and future ocean
        layers. Resolves the daily RTOFS run (its own cycle, cached separately in
        shared_state) and sets the SAME instance attributes get_gfs_state does
        (run_date_str / run_id / forecast_hour_str), so render_all_hours and the
        fieldstore reads work unchanged — they simply operate on the RTOFS run.

        RTOFS is one 00Z cycle/day; 'now' is hours-since-analysis, and a per-layer
        forecast_hour offset steps forward, identical in spirit to the GFS path.
        """
        from worldmap.lib.rtofs import resolve_rtofs_baseline

        baseline = getattr(self.map_data, "shared_state", {}).get("rtofs_baseline")
        if not baseline:
            baseline = resolve_rtofs_baseline()
            if not baseline:
                raise RuntimeError("Failed to sync RTOFS baseline from NOMADS.")
            self.map_data.shared_state["rtofs_baseline"] = baseline
            logger.debug(
                f"RTOFS Baseline Synced: {baseline['date_str']} {baseline['run']}Z"
            )

        now = datetime.now(timezone.utc)
        user_offset_hours = self.forecast_hour
        hours_since_run = int(
            round((now - baseline["timestamp"]).total_seconds() / 3600.0)
        )
        true_f_hour = max(0, hours_since_run + user_offset_hours)

        self.forecast_hour_str = f"{true_f_hour:03d}"
        self.run_date_str = baseline["date_str"]
        self.run_date_str_Y_M_D = baseline["date_str_Y_M_D"]
        self.run_id = baseline["run"]
        logger.debug(
            f"Section {self.section} get_rtofs_state: fhour {self.forecast_hour_str}; "
            f"date {self.run_date_str}; run {self.run_id}"
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