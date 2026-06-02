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


class StormwatchUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Stormwatch", map_data)
        self.set_output_path()

    def plot(self):
        import matplotlib.pyplot as plt
        from scipy.interpolate import RegularGridInterpolator
        import gc

        logger.debug(
            f"Plotting Stormwatch for {self.map_data.region.region_identifier}"
        )

        # Configuration (Default threshold of 1000 J/kg cuts out stable air)
        min_cape = self.settings.get("min_cape", 1000)
        alpha = self.settings.get("alpha", 0.6)
        key_position = (
            self.settings.get("key_position", "bottom-right").strip().lower()
        )
        key_fontsize = self.settings.get("key_fontsize", 10)

        # 1. Load Dataset and Clip Immediately
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")
        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
        ds = ds.sortby("longitude")

        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        ds_clipped = ds.sel(
            latitude=slice(lat_max + buf, lat_min - buf),
            longitude=slice(lon_min - buf, lon_max + buf),
        )

        # Extract both variables
        cape = ds_clipped["cape"].values.squeeze()
        cin = ds_clipped["cin"].values.squeeze()

        # --- THE METEOROLOGICAL MASKS ---
        # 1. Fuel Mask: Zero out areas with insufficient CAPE
        cape[cape < min_cape] = 0.0

        # 2. The Cap (CIN): Mask out areas where the "lid" is too strong (> 50 J/kg)
        # This effectively erases the CAPE where storms cannot physically fire
        cape_effective = np.where(cin > 50.0, 0.0, cape)

        lons = ds_clipped.longitude.values
        lats = ds_clipped.latitude.values

        # Explicit cleanup
        ds.close()
        del ds
        gc.collect()

        # 2. DYNAMIC RESAMPLING to prevent global OOM
        lon_span = abs(lon_max - lon_min)
        lat_span = abs(lat_max - lat_min)

        if lon_span > 90.0 or lat_span > 45.0:
            step = 0.15
            filter_sigma = 0.8
        else:
            step = 0.02
            filter_sigma = 1.2

        new_lats = np.arange(lats.min(), lats.max() + step, step)
        new_lons = np.arange(lons.min(), lons.max() + step, step)

        if lats[0] > lats[-1]:
            # Ensure we are using the newly masked 'cape_effective'
            lats_inc, cape_inc = lats[::-1], cape_effective[::-1, :]
        else:
            lats_inc, cape_inc = lats, cape_effective

        fn = RegularGridInterpolator(
            (lats_inc, lons), cape_inc, bounds_error=False, fill_value=0
        )

        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        cape_smooth = fn((mesh_lats, mesh_lons))

        # 3. Setup Plotting
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Define severe weather risk contours (J/kg)
        levels = [min_cape, 1500, 2000, 3000, 4000, 5000, 6000]

        # Transparent -> Yellow -> Orange -> Red -> Magenta -> Cyan -> White
        rgba_colors = [
            (1.0, 1.0, 0.0, alpha * 0.5),  # Faint Yellow (Marginal)
            (
                1.0,
                0.6,
                0.0,
                alpha,
            ),  # Orange (Slight - tweaked to 0.6 for better contrast)
            (1.0, 0.0, 0.0, alpha),  # Red (Enhanced/Moderate)
            (1.0, 0.0, 1.0, alpha),  # Magenta (High)
            (
                0.0,
                1.0,
                1.0,
                alpha,
            ),  # Electric Cyan (Extreme - Pops sharply against magenta)
            (1.0, 1.0, 1.0, alpha),  # Pure White (Off the charts - Unmissable)
        ]

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "storm_risk", rgba_colors, N=256
        )
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        cape_smooth = gaussian_filter(cape_smooth, sigma=filter_sigma)

        cf = plot.ax.contourf(
            new_lons,
            new_lats,
            cape_smooth,
            levels=levels,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max",
            antialiased=True,
            zorder=3,  # Sits slightly higher to render over temperature/SST
        )

        # 4. Draw the Key
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
            cf, cax=cbar_ax, orientation="horizontal", ticks=levels[:-1]
        )

        cbar.ax.xaxis.set_tick_params(
            color="white", labelsize=key_fontsize, labelcolor="white", pad=3
        )
        cbar.outline.set_edgecolor("white")
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title(
            "Storm Potential (Effective CAPE J/kg)",
            color="white",
            fontsize=key_fontsize,
            pad=5,
            weight="bold",
        )

        plot.save_figure(self.output_path)

        logger.debug("Finished Stormwatch plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_cape_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f000"
        if self.remote_data_update(
            remote_url=url,
            cache_file_path=self.grib_path,
            grib_targets=[":CAPE:surface:", ":CIN:surface:"],
        ):
            logger.info("Generating Stormwatch plot...")
            self.plot()
