#!/usr/bin/env python3
import os
import logging
import warnings
import gc
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.ndimage import gaussian_filter
from scipy.interpolate import RegularGridInterpolator

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

# Silence GRIB warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
gribapi_logger = logging.getLogger("gribapi.bindings")
gribapi_logger.setLevel(logging.ERROR)
gribapi_logger.propagate = False

logger = logging.getLogger(__name__)


class OzoneUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Ozone", map_data)
        # Default to Medium resolution if not specified
        self.level_of_detail = self.settings.get("level_of_detail", 2)
        self.lod_desc = None

    def save_ozone_key(self, output_path, cmap, norm, min_du, max_du):
        """Generates a standalone key image using a standardized naming strategy."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl
        import os

        # Standardize naming: take base name, add _key, append extension
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        key_fontsize = self.settings.get("key_fontsize", 8)

        fig, ax = plt.subplots(figsize=(4, 0.3))

        # Calculate 5 evenly spaced ticks between min and max
        calculated_ticks = np.linspace(min_du, max_du, 5)

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=calculated_ticks,
        )

        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter("%d"))
        cbar.ax.set_title(
            "Total Ozone (Dobson Units)",
            color="white",
            fontsize=key_fontsize,
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=8)

        # Save key separately
        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Ozone key to: {key_path}")

    def plot(self):
        """Renders the ozone layer with dynamic resampling for smoother visuals."""
        logger.debug(f"Plotting ozone layer to {self.output_path}...")

        palette_key = self.settings.get("palette", "critical").lower()
        bbox = self.map_region_bbox

        # Load Data
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")

        data_var = list(ds.data_vars)[0]
        raw_matrix = ds[data_var].values.squeeze()

        lat_raw = ds["latitude"].values
        lon_raw = ds["longitude"].values

        # Normalize and Sort Longitudes (-180 to 180)
        lon_norm = ((lon_raw + 180) % 360) - 180
        lon_sort_idx = np.argsort(lon_norm)
        lon_norm = lon_norm[lon_sort_idx]
        raw_matrix = raw_matrix[:, lon_sort_idx]

        # Apply Localized Clipping Masks
        lon_mask = (lon_norm >= bbox[0] - 1.0) & (lon_norm <= bbox[2] + 1.0)
        lat_mask = (lat_raw >= bbox[1] - 1.0) & (lat_raw <= bbox[3] + 1.0)

        lons_clipped = lon_norm[lon_mask]
        lats_clipped = lat_raw[lat_mask]
        display_data = raw_matrix[lat_mask, :][:, lon_mask]

        ds.close()
        del ds
        gc.collect()

        # DYNAMIC RESAMPLING (Level of Detail Logic)
        if self.level_of_detail == 3:
            step = 0.05  # High resolution (very smooth)
            filter_sigma = 1.2
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.125  # Medium resolution
            filter_sigma = 0.8
            self.lod_desc = "medium"
        else:
            step = 0.25  # Low resolution (Native GFS grid size)
            filter_sigma = 0.0  # No smoothing
            self.lod_desc = "low"

        # Ensure latitudes are strictly increasing for the Interpolator
        if lats_clipped[0] > lats_clipped[-1]:
            lats_inc, data_inc = lats_clipped[::-1], display_data[::-1, :]
        else:
            lats_inc, data_inc = lats_clipped, display_data

        fn = RegularGridInterpolator(
            (lats_inc, lons_clipped), data_inc, bounds_error=False, fill_value=0
        )

        new_lats = np.arange(lats_clipped.min(), lats_clipped.max() + step, step)
        new_lons = np.arange(lons_clipped.min(), lons_clipped.max() + step, step)
        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")

        smooth_data = fn((mesh_lats, mesh_lons))

        # Apply Gaussian smoothing only if LOD > 1
        if filter_sigma > 0:
            smooth_data = gaussian_filter(smooth_data, sigma=filter_sigma)

        # Mode Styling & Custom Colormaps
        min_du = 150
        max_du = 500
        norm = mcolors.Normalize(vmin=min_du, vmax=max_du)

        if palette_key == "critical":
            critical_du = self.settings.get("critical_du", 220.0)
            span = max(1, max_du - min_du)
            crit_point = max(0.0, min(1.0, (critical_du - min_du) / span))
            fade_point = min(1.0, crit_point + 0.05)

            color_stops = [0.0, crit_point, fade_point, 1.0]
            colors = [
                (1.0, 0.0, 1.0, 1.0),
                (1.0, 1.0, 0.0, 0.9),
                (0.0, 0.1, 0.3, 0.2),
                (0.0, 0.1, 0.3, 0.2),
            ]

            cmap_data = list(zip(color_stops, colors))
            cmap = mcolors.LinearSegmentedColormap.from_list(
                "critical_mask", cmap_data, N=256
            )
        else:
            palettes = {
                "plasma": "plasma_r",
                "viridis": "viridis_r",
                "inferno": "inferno_r",
                "turbo": "turbo_r",
            }
            cmap = plt.get_cmap(palettes.get(palette_key, "plasma_r"))

        # Canvas Initialization
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Generate 150 discrete contour levels to simulate a perfectly smooth gradient
        levels = np.linspace(min_du, max_du, 150)

        # Replaced pcolormesh with contourf for smoothed rendering
        plot.ax.contourf(
            new_lons,
            new_lats,
            smooth_data,
            levels=levels,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="both",
            antialiased=True,
            zorder=2,
        )

        # Save the map and the standalone key
        plot.save_figure(self.output_path)
        self.save_ozone_key(self.output_path, cmap, norm, min_du, max_du)

        # Memory cleanup
        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Finished Ozone plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_ozone_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(
            remote_url=url, cache_file_path=self.grib_path, grib_targets=[":TOZNE:"]
        ):
            self.plot()
            logger.info(f"Generated Ozone plot ({self.lod_desc} resolution)...")
