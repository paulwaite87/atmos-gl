#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import scipy.ndimage as ndimage
from PIL import Image

from matplotlib import patheffects

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

# Silence warnings from GRIB backend
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
logging.getLogger("gribapi.bindings").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


# --- WEBGL DATA ENCODER ---
def encode_data_texture(matrix_t0, matrix_t1, output_path, vmin, vmax):
    """
    Encodes two timesteps of scalar data (e.g., pressure) into the Red and Green
    channels of a PNG image for WebGL shader interpolation.
    """
    # 1. Ensure matrices are float arrays and dimensions match
    matrix_t0 = np.asarray(matrix_t0, dtype=np.float32)
    matrix_t1 = np.asarray(matrix_t1, dtype=np.float32)

    if matrix_t0.shape != matrix_t1.shape:
        raise ValueError(f"Matrix shape mismatch: {matrix_t0.shape} vs {matrix_t1.shape}")

    height, width = matrix_t0.shape

    # 2. Normalize data mathematically to a 0.0 - 1.0 range
    norm_t0 = (matrix_t0 - vmin) / (vmax - vmin)
    norm_t1 = (matrix_t1 - vmin) / (vmax - vmin)

    # 3. Clip out-of-bounds values
    norm_t0 = np.clip(norm_t0, 0.0, 1.0)
    norm_t1 = np.clip(norm_t1, 0.0, 1.0)

    # 4. Scale to 0 - 255 (8-bit)
    r_channel = (norm_t0 * 255.0).astype(np.uint8)
    g_channel = (norm_t1 * 255.0).astype(np.uint8)

    # 5. Create Blue and Alpha channels
    b_channel = np.zeros((height, width), dtype=np.uint8)
    a_channel = np.full((height, width), 255, dtype=np.uint8)

    # 6. Handle Missing Data (NaNs)
    nan_mask = np.isnan(matrix_t0) | np.isnan(matrix_t1)
    a_channel[nan_mask] = 0

    # 7. Stack and save
    rgba_array = np.dstack((r_channel, g_channel, b_channel, a_channel))
    img = Image.fromarray(rgba_array, mode="RGBA")
    img.save(output_path, format="PNG")
    logger.debug(f"Saved WebGL Data Texture to: {output_path}")
    return True


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)

        # We define a 6-hour animation loop step.
        # You could make this a config setting later.
        self.animation_step_hours = 6

        # We define the strict physical bounds for the WebGL shader.
        # 950hPa (Severe Cyclone) to 1050hPa (Strong Anti-Cyclone)
        self.VMIN_PRESSURE = 950.0
        self.VMAX_PRESSURE = 1050.0

    def plot(self):
        """Renders the standard isobar PNG AND the WebGL Data Texture."""
        logger.debug(f"Plotting isobars to {self.output_path}")

        # --- LOAD T0 (CURRENT) ---
        ds_t0 = xr.open_dataset(
            self.grib_path_t0,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
            },
        )
        p_t0 = ds_t0["prmsl"].values / 100.0
        lons, lats = ds_t0["longitude"].values, ds_t0["latitude"].values

        # Standardize longitudes to -180..180
        lons = ((lons + 180) % 360) - 180
        lon_idx = np.argsort(lons)
        lons = lons[lon_idx]
        p_t0 = p_t0[:, lon_idx]
        p_t0_smooth = ndimage.gaussian_filter(p_t0, sigma=1.2)

        # --- FALLBACK: Standard PNG Generation (Using T0) ---
        plot = Plot(self.map_data.region)
        plot.get_figure()

        step = self.settings.get("isobar_step", 4)
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", "white")
        f_size = self.settings.get("label_fontsize", 10)
        thickness = self.settings.get("linewidth", 1.0)
        alpha_val = self.settings.get("alpha", 1.0)

        line_effect = [
            patheffects.withStroke(
                linewidth=thickness + 1.0, foreground="black", alpha=alpha_val * 0.4
            )
        ]

        cs = plot.ax.contour(
            lons, lats, p_t0_smooth,
            levels=levels, colors=color,
            linewidths=thickness, alpha=alpha_val,
            transform=ccrs.PlateCarree(),
        )

        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(line_effect)

        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels:
            for txt in labels:
                txt.set_alpha(alpha_val)
                txt.set_path_effects(line_effect)

        plot.save_figure(self.output_path)
        ds_t0.close()

        # --- WEBGL: Generate Data Texture ---
        # Only attempt this if the T1 (Next) file successfully downloaded
        if os.path.exists(self.grib_path_t1):
            logger.debug("Generating Isobars WebGL Data Texture...")
            ds_t1 = xr.open_dataset(
                self.grib_path_t1,
                engine="cfgrib",
                backend_kwargs={
                    "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
                },
            )
            p_t1 = ds_t1["prmsl"].values / 100.0
            p_t1 = p_t1[:, lon_idx]
            p_t1_smooth = ndimage.gaussian_filter(p_t1, sigma=1.2)

            # Construct the output path for the data texture
            base, ext = os.path.splitext(self.output_path)
            data_texture_path = f"{base}_data.png"

            # Interpolation directly mapping our standard grid arrays
            # Cartopy handles this for the visual plot, but for the raw data texture
            # we just pass the smoothed arrays directly.
            encode_data_texture(
                matrix_t0=p_t0_smooth,
                matrix_t1=p_t1_smooth,
                output_path=data_texture_path,
                vmin=self.VMIN_PRESSURE,
                vmax=self.VMAX_PRESSURE
            )
            ds_t1.close()
        else:
            logger.warning("Skipping Isobars WebGL Data Texture: T1 file missing.")

        logger.debug("Finished Isobars processing.")

    def run(self):
        self.exit_if_disabled()
        self.get_gfs_state()

        # Calculate true forecast hours for T0 (Now) and T1 (Next)
        # self.forecast_hour_str is calculated in get_gfs_state (e.g., '000')
        f_hour_t0 = int(self.forecast_hour_str)
        f_hour_t1 = f_hour_t0 + self.animation_step_hours

        # Format for GFS urls (e.g., f000, f006)
        f_str_t0 = f"{f_hour_t0:03d}"
        f_str_t1 = f"{f_hour_t1:03d}"

        # Define file paths
        self.grib_path_t0 = os.path.join(self.workdir, f"data/gfs_isobars_{f_str_t0}.grib2")
        self.grib_path_t1 = os.path.join(self.workdir, f"data/gfs_isobars_{f_str_t1}.grib2")

        # Construct URLs based on the SAME run, just different forecast hours
        url_t0 = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{f_str_t0}"
        url_t1 = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{f_str_t1}"

        # Track if we need to plot
        needs_plot = False

        # Download T0
        if self.remote_data_update(
                remote_url=url_t0,
                cache_file_path=self.grib_path_t0,
                grib_targets=[":PRMSL:mean sea level:"],
        ):
            needs_plot = True

        # Download T1 (we pass cache_was_updated down implicitly by evaluating remote_data_update)
        if self.remote_data_update(
                remote_url=url_t1,
                cache_file_path=self.grib_path_t1,
                grib_targets=[":PRMSL:mean sea level:"],
        ):
            needs_plot = True

        if needs_plot:
            logger.info("Generating Isobars plot and WebGL data texture...")
            self.plot()