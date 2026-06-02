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
        self.set_output_path()

    def plot(self):
        """Renders the ozone layer as a direct geographical color mesh."""
        logger.debug(f"Plotting ozone layer to {self.output_path}...")

        alpha = self.settings.get("alpha", 0.4)
        key_position = (
            self.settings.get("key_position", "bottom-right").strip().lower()
        )
        key_fontsize = self.settings.get("key_fontsize", 10)
        palette_key = self.settings.get("palette", "critical").lower()
        bbox = self.map_region_bbox

        # 1. Load Data
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")

        # cfgrib usually parses TOZNE to a variable named 'tozne'
        # If it uses a fallback like 'unknown', we grab the first data variable dynamically
        data_var = list(ds.data_vars)[0]
        raw_matrix = ds[data_var].values.squeeze()

        lat_raw = ds["latitude"].values
        lon_raw = ds["longitude"].values

        # 2. Normalize and Sort Longitudes (-180 to 180)
        lon_norm = ((lon_raw + 180) % 360) - 180
        lon_sort_idx = np.argsort(lon_norm)
        lon_norm = lon_norm[lon_sort_idx]
        raw_matrix = raw_matrix[:, lon_sort_idx]

        # 3. Apply Localized Clipping Masks
        lon_mask = (lon_norm >= bbox[0] - 1.0) & (lon_norm <= bbox[2] + 1.0)
        lat_mask = (lat_raw >= bbox[1] - 1.0) & (lat_raw <= bbox[3] + 1.0)

        lons_clipped = lon_norm[lon_mask]
        lats_clipped = lat_raw[lat_mask]
        display_data = raw_matrix[lat_mask, :][:, lon_mask]

        ds.close()
        del ds
        gc.collect()

        # 4. Mode Styling & Custom Colormaps
        vmin = self.settings.get("min_du", 150)
        vmax = self.settings.get("max_du", 500)
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

        if palette_key == "critical":
            # 220 DU is the official definition of an ozone hole
            critical_du = self.settings.get("critical_du", 220.0)

            # Calculate where the critical threshold falls on the 0.0 to 1.0 color scale
            span = max(1, vmax - vmin)
            crit_point = max(0.0, min(1.0, (critical_du - vmin) / span))

            # Set the point where the data becomes totally invisible (just above the threshold)
            fade_point = min(1.0, crit_point + 0.05)

            # Map the exact gradient stops: [Position (0 to 1), (R, G, B, Alpha)]
            color_stops = [0.0, crit_point, fade_point, 1.0]
            colors = [
                (1.0, 0.0, 1.0, 1.0),  # Lowest values: Bright Opaque Magenta
                (1.0, 1.0, 0.0, 0.9),  # Critical threshold: Bright Opaque Yellow
                (0.0, 0.1, 0.3, 0.2),  # Safe area: Very faint, translucent dark blue
                (
                    0.0,
                    0.1,
                    0.3,
                    0.2,
                ),  # Highest values: Very faint, translucent dark blue
            ]

            cmap_data = list(zip(color_stops, colors))
            cmap = mcolors.LinearSegmentedColormap.from_list(
                "critical_mask", cmap_data, N=256
            )
        else:
            # Fallback to standard reversed colormaps
            palettes = {
                "plasma": "plasma_r",
                "viridis": "viridis_r",
                "inferno": "inferno_r",
                "turbo": "turbo_r",
            }
            cmap = plt.get_cmap(palettes.get(palette_key, "plasma_r"))

        # 5. Canvas Initialization
        plot = Plot(self.map_data.region)
        plot.get_figure()

        mesh = plot.ax.pcolormesh(
            lons_clipped,
            lats_clipped,
            display_data,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            alpha=alpha,
            shading="nearest",
            rasterized=True,
            zorder=2,
        )

        # 6. Colorbar Overlay
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03],
        }
        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor("#111111")
        cbar_ax.patch.set_alpha(alpha)

        calculated_ticks = np.linspace(vmin, vmax, 5)
        cbar = plt.colorbar(
            mesh, cax=cbar_ax, orientation="horizontal", ticks=calculated_ticks
        )

        cbar.ax.xaxis.set_tick_params(
            color="white", labelsize=key_fontsize, labelcolor="white", pad=3
        )
        cbar.outline.set_edgecolor("white")
        cbar.outline.set_linewidth(0.5)
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter("%d"))
        cbar.ax.set_title(
            "Total Ozone (DU)",
            color="white",
            fontsize=key_fontsize,
            pad=5,
            weight="bold",
        )

        plot.save_figure(self.output_path)

        logger.debug("Successfully rendered GFS Ozone layer.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_ozone_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(
            remote_url=url, cache_file_path=self.grib_path, grib_targets=[":TOZNE:"]
        ):
            logger.info("Generating Ozone plot...")
            self.plot()
