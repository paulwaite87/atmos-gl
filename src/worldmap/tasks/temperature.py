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

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class TemperatureUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Temperature", map_data)

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

    def plot(self):
        """Plots an underlying 2-meter surface temperature heatmap
        with optional isotherm contour lines and an overlay color key.
        """
        from scipy.interpolate import griddata

        logger.debug(
            f"Plotting Temperature Data for {self.map_data.region.region_identifier}"
        )

        alpha_setting = self.settings.get("alpha", 0.75)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)
        mode = self.settings.get("mode", "absolute").strip().lower()

        show_freezing_line = self.settings.get("show_freezing_line", True)

        # Key style configurations
        key_position = self.settings.get("key_position", "bottom-right")
        key_fontsize = self.settings.get("key_fontsize", 10)

        # Open Dataset with cfgrib filtering for 2-meter above ground level
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}
            },
        )

        lon_raw = ((ds["longitude"].values + 180) % 360) - 180
        lat_raw = ds["latitude"].values
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        lon_inside = (lon_raw >= lon_min - buf) & (lon_raw <= lon_max + buf)
        lat_inside = (lat_raw >= lat_min - buf) & (lat_raw <= lat_max + buf)

        temp_key = "t2m" if "t2m" in ds else "2t"

        if lon_raw.ndim == 1 and lat_raw.ndim == 1:
            spatial_mask = lat_inside[:, np.newaxis] & lon_inside[np.newaxis, :]
            mesh_lon_raw, mesh_lat_raw = np.meshgrid(lon_raw, lat_raw)

            temp_raw_k = ds[temp_key].values[spatial_mask]
            lons_clipped = mesh_lon_raw[spatial_mask]
            lats_clipped = mesh_lat_raw[spatial_mask]
        else:
            mask = lon_inside & lat_inside
            temp_raw_k = ds[temp_key].values[mask]
            lons_clipped = lon_raw[mask]
            lats_clipped = lat_raw[mask]

        ds.close()
        del ds
        gc.collect()

        valid = ~np.isnan(temp_raw_k)
        if not np.any(valid):
            logger.warning("No temperature coordinates found within the region slice.")
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        temp_raw_c = temp_raw_k[valid] - 273.15

        # 3. Build unified processing grid mesh
        grid_lon = np.linspace(lon_min, lon_max, 300)
        grid_lat = np.linspace(lat_min, lat_max, 300)
        mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

        # Absolute temperature grid (always retained for the freezing line overlay)
        temp_grid = griddata(
            points, temp_raw_c, (mesh_lon, mesh_lat), method="linear", fill_value=np.nan
        )

        # 4. Dynamic Mode Processing (Absolute vs Automated Anomaly)
        if mode == "anomaly":
            spatial_mean = np.nanmean(temp_grid)
            display_data = temp_grid - spatial_mean

            cmap_name = "coolwarm"
            cmap = plt.get_cmap(cmap_name)

            # AUTOMATED RANGE ENGINE
            # Captures 98th percentile of absolute deviations to ignore single extreme outliers
            abs_anomalies = np.abs(display_data)
            calculated_range = float(np.nanpercentile(abs_anomalies, 98))
            anomaly_range = max(
                0.5, calculated_range
            )  # Safety buffer to prevent 0-scale collapse

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

        # 5. Initialize Core Canvas
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # 6. Render Heatmap (Using mode-dependent data)
        cf = plot.ax.contourf(
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

        # 7. Render Freezing Line Isotherm (Always using absolute temp_grid!)
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

        # 8. ENHANCEMENT: DYNAMIC ADJUSTED COLOR KEY OVERLAY
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03],
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor("#111111")
        cbar_ax.patch.set_alpha(0.4)

        cbar = plt.colorbar(
            cf, cax=cbar_ax, orientation="horizontal", ticks=calculated_ticks
        )

        # Style the color scale numbers
        cbar.ax.xaxis.set_tick_params(
            color="white", labelsize=key_fontsize, labelcolor="white", pad=3
        )
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter(tick_format))
        cbar.outline.set_edgecolor("white")
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title(
            title_text, color="white", fontsize=key_fontsize, pad=5, weight="bold"
        )

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug(
            f"Temperature ({mode} mode) plotting sequence completed successfully."
        )

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_temp_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(remote_url=url, cache_file_path=self.grib_path):
            logger.info("Generating Temperature plot...")
            self.plot()
