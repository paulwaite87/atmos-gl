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
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        self.lod_desc = None

    def save_stormwatch_key(self, output_path, levels, rgba_colors):
        """Generates a standalone key image for the Stormwatch layer."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl

        # Standardize naming: append _key to the base filename
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        fig, ax = plt.subplots(figsize=(4, 0.3))

        # Recreate the colormap and norm for the standalone bar
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "storm_risk", rgba_colors, N=256
        )
        norm = mpl.colors.BoundaryNorm(levels, cmap.N)

        # Draw the colorbar
        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=levels[:-1],
        )

        cbar.ax.set_title(
            "Storm Potential (Effective CAPE J/kg)",
            color="white",
            fontsize=self.settings.get("key_fontsize", 8),
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=6)

        # Save with transparency
        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Stormwatch key to: {key_path}")

    def plot(self):
        from scipy.interpolate import RegularGridInterpolator
        import gc

        logger.debug(
            f"Plotting Stormwatch for {self.map_data.region.region_identifier}"
        )

        # Configuration (Default threshold of 1000 J/kg cuts out stable air)
        min_cape = self.settings.get("min_cape", 1000)
        alpha = self.settings.get("alpha", 0.6)

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

        plot.ax.contourf(
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

        # 4. Save the map and the standalone key
        plot.save_figure(self.output_path)

        # Pass the levels and colors down to the key generator
        self.save_stormwatch_key(self.output_path, levels, rgba_colors)

        # Memory cleanup
        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Finished Stormwatch plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = self.cache_path(f"gfs_cape_{self.forecast_hour_str}.grib2")

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f000"
        if self.remote_data_update(
            remote_url=url,
            cache_file_path=self.grib_path,
            grib_targets=[":CAPE:surface:", ":CIN:surface:"],
        ):
            self.plot()
            logger.info(f"Generated stormwatch plot ({self.lod_desc} resolution)...")
