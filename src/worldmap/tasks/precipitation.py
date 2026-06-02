#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.ndimage import gaussian_filter

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PrecipitationUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Precipitation", map_data)
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        self.lod_desc = None

        self.PALETTES = {
            "standard": [
                (0.0, 1.0, 1.0),
                (0.0, 0.5, 1.0),
                (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.5, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0),
            ],
            "ocean_blue": [
                (0.8, 0.9, 1.0),
                (0.6, 0.8, 1.0),
                (0.4, 0.6, 1.0),
                (0.2, 0.4, 1.0),
                (0.0, 0.2, 0.8),
                (0.0, 0.0, 0.6),
                (0.0, 0.0, 0.4),
            ],
            "high_contrast": [
                (0.0, 0.9, 0.0),
                (0.0, 0.6, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.6, 0.0),
                (1.0, 0.0, 0.0),
                (0.7, 0.0, 0.0),
                (1.0, 0.0, 1.0),
            ],
        }

    def plot(self):
        """Renders precipitation with early clipping to prevent memory exhaustion."""
        import matplotlib.pyplot as plt
        from scipy.interpolate import RegularGridInterpolator
        import gc  # Garbage collector

        logger.debug(
            f"Plotting precipitation for {self.map_data.region.region_identifier}"
        )

        min_rate = self.settings.get("min_mm_hr", 0.1)
        alpha = self.settings.get("alpha", 0.5)
        palette_name = self.settings.get("palette", "standard")

        # Parse key layout configurations
        key_position = (
            self.settings.get("key_position", "bottom-right").strip().lower()
        )
        key_fontsize = self.settings.get("key_fontsize", 10)

        # 1. Load Dataset and Clip Immediately
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")

        # Standardize longitudes to -180..180
        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
        ds = ds.sortby("longitude")

        # Define BBox with a small buffer for smooth edges
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        # SLICE EARLY: This is the primary memory-saving step
        ds_clipped = ds.sel(
            latitude=slice(lat_max + buf, lat_min - buf),
            longitude=slice(lon_min - buf, lon_max + buf),
        )

        prate = ds_clipped["prate"].values.squeeze() * 3600.0
        # Apply the minimum threshold to clip out trace noise
        prate[prate < min_rate] = 0.0

        lons = ds_clipped.longitude.values
        lats = ds_clipped.latitude.values

        # Explicit cleanup of the large dataset
        ds.close()
        del ds
        gc.collect()

        # Sampling sizes according to user setting
        if self.level_of_detail == 1:
            step = 0.10  # High resolution; ~162M points
            filter_sigma = 1.0
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.125  # Medium resolution
            filter_sigma = 0.9
            self.lod_desc = "medium"
        else:
            step = 0.15  # Low resolution; ~2.8M points
            filter_sigma = 0.8
            self.lod_desc = "low"

        new_lats = np.arange(lats.min(), lats.max() + step, step)
        new_lons = np.arange(lons.min(), lons.max() + step, step)

        # Handle latitude ordering for Interpolator (must be strictly increasing)
        if lats[0] > lats[-1]:
            lats_inc, prate_inc = lats[::-1], prate[::-1, :]
        else:
            lats_inc, prate_inc = lats, prate

        fn = RegularGridInterpolator(
            (lats_inc, lons), prate_inc, bounds_error=False, fill_value=0
        )

        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        prate_smooth = fn((mesh_lats, mesh_lons))

        # 3. Setup Plotting
        plot = Plot(self.map_data.region)
        plot.get_figure()

        levels = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0]
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        rgba_colors = [(*c, alpha) for c in base_colors]

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "smooth_precip", rgba_colors, N=256
        )
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        # 4. Render Heatmap Contour
        prate_smooth = gaussian_filter(prate_smooth, sigma=filter_sigma)
        cf = plot.ax.contourf(
            new_lons,
            new_lats,
            prate_smooth,
            levels=levels,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max",
            antialiased=True,
            zorder=2,
        )

        # 5. ENHANCEMENT: DYNAMIC ADJUSTED COLOR KEY OVERLAY
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

        key_ticks = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]

        cbar = plt.colorbar(cf, cax=cbar_ax, orientation="horizontal", ticks=key_ticks)

        cbar.ax.xaxis.set_tick_params(
            color="white", labelsize=key_fontsize, labelcolor="white", pad=3
        )
        cbar.outline.set_edgecolor("white")
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title(
            "Precipitation (mm/hr)",
            color="white",
            fontsize=key_fontsize,
            pad=5,
            weight="bold",
        )

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Finished Precipitation plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_precip_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(
            remote_url=url,
            cache_file_path=self.grib_path,
            grib_targets=[":PRATE:surface:"],
        ):
            self.plot()
            logger.info(f"Generated precipitation plot ({self.lod_desc} resolution)...")
