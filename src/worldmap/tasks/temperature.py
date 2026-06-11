#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import gc

from scipy.ndimage import gaussian_filter
from scipy.interpolate import RegularGridInterpolator

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


def _nan_safe_gaussian(a, sigma):
    if sigma <= 0 or not np.isnan(a).any():
        return gaussian_filter(a, sigma=sigma) if sigma > 0 else a
    m = np.isnan(a)
    filled = gaussian_filter(np.where(m, 0.0, a), sigma=sigma)
    weight = gaussian_filter((~m).astype(float), sigma=sigma)
    out = filled / np.where(weight == 0, 1.0, weight)
    out[weight == 0] = np.nan
    return out


class TemperatureUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Temperature", map_data)
        # Default to Medium resolution if not specified
        self.level_of_detail = self.settings.get("level_of_detail", 2)
        self.lod_desc = None

        # DESIGNED GRADIENTS FOR AIR TEMPERATURE (-40C to +45C)
        self.PALETTES = {
            "global_thermal": [
                (0.2, 0.0, 0.4),  # -40C: Deep Violet
                (0.0, 0.2, 0.6),  # -20C: Navy Blue
                (0.0, 0.8, 1.0),  # -5C: Frost Cyan
                (1.0, 1.0, 1.0),  # 0C: Freezing White
                (1.0, 0.9, 0.2),  # 15C: Pleasant Yellow
                (1.0, 0.4, 0.0),  # 30C: Hot Orange
                (0.6, 0.0, 0.1),  # 45C: Searing Crimson
            ],
            "extreme_contrast": [
                (0.7, 0.0, 0.7),  # -40C: Intense Magenta
                (0.0, 0.2, 1.0),  # -20C: Electric Blue
                (0.0, 0.9, 1.0),  # -5C: Bright Cyan
                (0.0, 0.9, 0.0),  # 5C: Vivid Neon Green
                (1.0, 1.0, 0.0),  # 18C: Blazing Yellow
                (1.0, 0.5, 0.0),  # 30C: Safety Orange
                (1.0, 0.0, 0.0),  # 38C: Pure Red
                (0.9, 0.7, 1.0),  # 45C: White-Hot Purple
            ],
            "twilight_gradient": [
                (0.1, 0.1, 0.3),  # -40C: Dark Indigo
                (0.2, 0.4, 0.6),  # -20C: Muted Steel Blue
                (0.5, 0.7, 0.7),  # 0C: Slate
                (0.8, 0.7, 0.5),  # 15C: Warm Sand
                (0.8, 0.4, 0.3),  # 30C: Burnt Terracotta
                (0.5, 0.1, 0.1),  # 45C: Deep Brick
            ],
        }

    def save_temperature_key(
        self, output_path, cmap, norm, ticks, title_text, tick_format
    ):
        """Generates a standalone key image using dynamic formatting based on mode."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl

        # Standardize naming
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        key_fontsize = self.settings.get("key_fontsize", 10)

        fig, ax = plt.subplots(figsize=(4, 0.3))

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=ticks,
        )

        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter(tick_format))

        cbar.ax.set_title(
            title_text, color="white", fontsize=key_fontsize, pad=2, weight="bold"
        )
        cbar.ax.tick_params(colors="white", labelsize=8)

        # Save key separately
        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Temperature key to: {key_path}")

    def plot(self):
        """Plots a global 2-meter surface temperature heatmap
        with dynamic resampling for smooth visuals and crisp isotherms.
        """
        logger.debug(
            f"Plotting Temperature Data for {self.map_data.region.region_identifier}"
        )

        alpha_setting = float(self.settings.get("alpha", 75) / 100)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)
        mode = self.settings.get("mode", "absolute").strip().lower()
        show_freezing_line = self.settings.get("show_freezing_line", True)
        bbox = self.map_region_bbox

        # Load Dataset
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}
            },
        )

        temp_key = "t2m" if "t2m" in ds else "2t"
        raw_matrix = (
            ds[temp_key].values.squeeze() - 273.15
        )  # Convert Kelvin to Celsius immediately

        lon_raw = ds["longitude"].values
        lat_raw = ds["latitude"].values

        # Normalize and Sort Longitudes (-180 to 180) to avoid seam line artifacts
        lon_norm = ((lon_raw + 180) % 360) - 180
        lon_sort_idx = np.argsort(lon_norm)
        lon_norm = lon_norm[lon_sort_idx]
        raw_matrix = raw_matrix[:, lon_sort_idx]

        # Ensure latitudes are strictly increasing for the RegularGridInterpolator
        if lat_raw[0] > lat_raw[-1]:
            lat_inc = lat_raw[::-1]
            raw_matrix = raw_matrix[::-1, :]
        else:
            lat_inc = lat_raw

        # Level of Detail Resolution Strategies (Global Scale Performance Optimized)
        if self.level_of_detail == 3:
            step = 0.05  # High Resolution (7200x3600 grid points)
            filter_sigma = 1.2
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.10  # Medium Resolution (3600x1800 grid points)
            filter_sigma = 0.8
            self.lod_desc = "medium"
        else:
            step = 0.25  # Low Resolution (Native 1440x720 GFS Grid size)
            filter_sigma = 0.0  # Raw data, no smoothing
            self.lod_desc = "low"

        # High-Speed Regular Grid Interpolation Pipeline
        fn = RegularGridInterpolator(
            (lat_inc, lon_norm), raw_matrix, bounds_error=False, fill_value=np.nan
        )

        from .common import MERCATOR_LAT_LIMIT

        lat_lo = max(bbox[1], float(lat_inc.min()), -MERCATOR_LAT_LIMIT)
        lat_hi = min(bbox[3], float(lat_inc.max()), MERCATOR_LAT_LIMIT)
        lon_lo = max(bbox[0], float(lon_norm.min()))
        lon_hi = min(bbox[2], float(lon_norm.max()))  # = 179.75, not 180.0

        grid_lat = np.linspace(lat_lo, lat_hi, int(round((lat_hi - lat_lo) / step)) + 1)
        grid_lon = np.linspace(lon_lo, lon_hi, int(round((lon_hi - lon_lo) / step)) + 1)
        mesh_lat, mesh_lon = np.meshgrid(grid_lat, grid_lon, indexing="ij")
        temp_grid = fn((mesh_lat, mesh_lon))

        ds.close()
        del ds
        gc.collect()

        # Smooth without letting any residual NaN bloom across the grid
        temp_grid = _nan_safe_gaussian(temp_grid, filter_sigma)

        # Dynamic Mode Processing (Absolute vs Automated Anomaly)
        if mode == "anomaly":
            spatial_mean = np.nanmean(temp_grid)
            display_data = temp_grid - spatial_mean

            cmap = plt.get_cmap("coolwarm")

            # Automated symmetric range calculation
            abs_anomalies = np.abs(display_data)
            calculated_range = float(np.nanpercentile(abs_anomalies, 98))
            anomaly_range = max(0.5, calculated_range)

            vmin, vmax = -anomaly_range, anomaly_range
            levels = np.linspace(vmin, vmax, 86)
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)

            title_text = "Air Temp Regional Anomaly (°C)"
            calculated_ticks = np.linspace(vmin, vmax, 5)
            tick_format = "%.1f"
        else:
            display_data = temp_grid

            palette_name = self.settings.get("palette", "global_thermal")
            if palette_name not in self.PALETTES:
                palette_name = "global_thermal"

            custom_rgba_list = [
                (r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]
            ]
            cmap = mcolors.LinearSegmentedColormap.from_list(
                "surface_temp", custom_rgba_list, N=256
            )

            vmin, vmax = -40.0, 45.0
            levels = np.linspace(vmin, vmax, 86)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

            title_text = "Temperature (°C)"
            calculated_ticks = [-40, -20, 0, 15, 30, 45]
            tick_format = "%d"

        # Initialize Canvas
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # 6. Render Heatmap Contour
        # The crucial fix: Notice the `.T` transpose on the data arrays.
        # meshgrid with indexing="ij" creates arrays in (lat, lon) shape.
        # contourf expects (lon, lat) shape to match x/y coordinates.
        plot.ax.contourf(
            grid_lon,
            grid_lat,
            display_data,
            levels=levels,
            cmap=cmap,
            norm=norm,
            extend="both",
            antialiased=True,
            transform=ccrs.PlateCarree(),
            zorder=2,
        )

        # 7. Render Freezing Line Isotherm
        if show_freezing_line:
            plot.ax.contour(
                grid_lon,
                grid_lat,
                temp_grid,
                levels=[0.0],
                colors=["#00FFFF"],
                linewidths=[1.8],
                linestyles=["dashed"],
                alpha=0.9,
                transform=ccrs.PlateCarree(),
                zorder=4,
            )

        # Save main figure and standalone key
        plot.save_figure(self.output_path)

        self.save_temperature_key(
            self.output_path, cmap, norm, calculated_ticks, title_text, tick_format
        )

        # Clean up
        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.info(
            f"Successfully generated global Temperature plot ({self.lod_desc} resolution)."
        )

    def run(self):
        self.exit_if_disabled()
        self.get_gfs_state()
        self.grib_path = self.cache_path(f"gfs_temp_{self.forecast_hour_str}.grib2")

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(remote_url=url, cache_file_path=self.grib_path):
            self.plot()
