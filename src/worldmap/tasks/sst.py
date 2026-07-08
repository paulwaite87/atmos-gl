#!/usr/bin/env python3
import os
import logging
import gc
import numpy as np
import xarray as xr
import matplotlib as mpl
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

logger = logging.getLogger(__name__)


class SSTUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "sst", map_data)
        self.mode = self.settings.get("mode", "absolute").strip().lower()

    def plot(self):
        alpha = float(self.settings.get("alpha", 40) / 100)
        bbox = self.map_region_bbox

        # --- Data Loading ---
        ds = xr.open_dataset(self.nc_path, chunks={"time": 1})
        latest_slice = ds.isel(time=-1)

        lat_raw = latest_slice["lat"].values
        lon_raw = latest_slice["lon"].values

        # Dynamically target 'anom' for anomaly mode, or 'sst' for absolute mean mode
        data_var = "anom" if self.mode == "anomaly" else "sst"
        raw_matrix = latest_slice[data_var].values.squeeze()

        # Cleanly transform NOAA's 0-360 range into a standard -180 to +180 baseline
        lon_norm = ((lon_raw + 180) % 360) - 180

        # Sort along longitudes to avoid geometric rendering seams or distortions
        lon_sort_idx = np.argsort(lon_norm)
        lon_norm = lon_norm[lon_sort_idx]
        raw_matrix = raw_matrix[:, lon_sort_idx]

        # Create localized clipping masks matching the current dashboard view limits
        lon_mask = (lon_norm >= bbox[0] - 1.0) & (lon_norm <= bbox[2] + 1.0)
        lat_mask = (lat_raw >= bbox[1] - 1.0) & (lat_raw <= bbox[3] + 1.0)

        # Slice grid matrices to current boundary context
        lons_clipped = lon_norm[lon_mask]
        lats_clipped = lat_raw[lat_mask]
        display_data = raw_matrix[lat_mask, :][:, lon_mask]

        ds.close()
        del ds
        gc.collect()

        # --- Dynamic Mode Styling Pipeline ---
        if self.mode == "anomaly":
            # Isolates 98th percentile of absolute variance on screen for stable scale bounds
            abs_anomalies = np.abs(display_data)
            calculated_range = (
                float(np.nanpercentile(abs_anomalies, 98))
                if np.any(~np.isnan(abs_anomalies))
                else 4.0
            )
            anomaly_range = max(0.5, calculated_range)

            vmin, vmax = -anomaly_range, anomaly_range
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
            cmap = mpl.cm.get_cmap("coolwarm")
            title_text = "SST Climatological Anomaly (°C)"
            tick_format = "%.1f"
        else:
            # Absolute Mode Configurations
            vmin = self.settings.get("min_c", 0)
            vmax = self.settings.get("max_c", 32)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

            palette_key = self.settings.get("palette", "thermal").lower()
            palettes = {
                "thermal": "magma",
                "vivid": "turbo",
                "deep": "viridis",
                "ocean": "inferno",
            }
            cmap = mpl.cm.get_cmap(palettes.get(palette_key, "magma"))
            title_text = "Sea Surface Temp (°C)"
            tick_format = "%d"

        # --- Canvas Initialization ---
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Render complete mapped geographic array using exact pixel cell boundaries
        plot.ax.pcolormesh(
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

        plot.save_figure(self.output_path)
        calculated_ticks = np.linspace(vmin, vmax, 5)
        self.save_key_image(
            self.output_path,
            cmap,
            norm,
            calculated_ticks,
            title_text,
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            tick_format=tick_format,
            weight="bold",
        )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug(f"Successfully rendered raw NOAA OISST map in {self.mode} mode.")

    def run(self, max_hours=None):
        # max_hours is a no-op here -- SST renders once per cycle, not per forecast
        # hour, so it has nothing to cap. Accepted only so layer_builder's dispatch can
        # call every TASK_CLASSES entry's run() the same way.
        # The data_collector now owns the OISST download; we just render from the shared
        # cache it maintains. Read it, and (re)render only when the cache is newer than
        # our output, so we don't repaint every cycle for an unchanged daily field.
        from worldmap.lib.oisst import oisst_cache_path

        self.nc_path = oisst_cache_path(self.workdir, self.mode)
        if not os.path.exists(self.nc_path):
            logger.info(
                f"SST: cache {os.path.basename(self.nc_path)} not present yet "
                "(data collector hasn't fetched it); skipping."
            )
            return

        out = self.output_path
        if (
            out
            and os.path.exists(out)
            and os.path.getmtime(out) >= os.path.getmtime(self.nc_path)
        ):
            logger.debug("SST: output already up to date with cache; skipping render.")
            return

        logger.info(f"Generating SST {self.mode} plot...")
        self.plot()
