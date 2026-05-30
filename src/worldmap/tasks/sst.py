#!/usr/bin/env python3
import os
import logging
import gc
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
from datetime import datetime, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

logger = logging.getLogger(__name__)


class SSTUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "sst", map_data)
        self.set_output_path()

        # Parse operational mode
        self.mode = self.settings.get("mode", fallback="absolute").strip().lower()

    def download_data(self):
        """
        Downloads the appropriate yearly NOAA OISST v2 High-Res dataset using the generated target URL.
        """
        url = self.target_url

        try:
            logger.debug(f"Checking freshness of remote NOAA Dataset ({self.mode}): {url}")
            response = requests.head(url, timeout=10)

            if response.status_code == 200:
                if os.path.exists(self.nc_path):
                    remote_mtime_str = response.headers.get('Last-Modified')
                    if remote_mtime_str:
                        remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                            tzinfo=timezone.utc)
                        local_mtime = datetime.fromtimestamp(os.path.getmtime(self.nc_path), tz=timezone.utc)
                        if remote_mtime <= local_mtime:
                            logger.info(f"Local NOAA SST {self.mode} cache is up to date.")
                            return False

                logger.info(f"Downloading NOAA SST {self.mode} dataset...")
                os.makedirs(os.path.dirname(self.nc_path), exist_ok=True)

                with requests.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(self.nc_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            f.write(chunk)
                return True

        except Exception as e:
            logger.warning(f"Failed to download live NOAA dataset: {e}")

        if os.path.exists(self.nc_path):
            logger.info(f"Falling back to existing local NOAA {self.mode} netCDF file.")
            return False

        raise RuntimeError(f"Critical Error: Missing local and remote access to NOAA SST {self.mode} dataset.")

    def plot(self):
        alpha = self.settings.getfloat("alpha", fallback=0.4)
        key_position = self.settings.get("key_position", fallback="bottom-right").strip().lower()
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)
        bbox = self.map_region_bbox

        # --- Data Loading ---
        ds = xr.open_dataset(self.nc_path, chunks={'time': 1})
        latest_slice = ds.isel(time=-1)

        lat_raw = latest_slice['lat'].values
        lon_raw = latest_slice['lon'].values

        # Dynamically target 'anom' for anomaly mode, or 'sst' for absolute mean mode
        data_var = 'anom' if self.mode == "anomaly" else 'sst'
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
            calculated_range = float(np.nanpercentile(abs_anomalies, 98)) if np.any(~np.isnan(abs_anomalies)) else 4.0
            anomaly_range = max(0.5, calculated_range)

            vmin, vmax = -anomaly_range, anomaly_range
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
            cmap = plt.get_cmap("coolwarm")
            title_text = "SST Climatological Anomaly (°C)"
            tick_format = '%.1f'
        else:
            # Absolute Mode Configurations
            vmin = self.settings.getint("min_c", fallback=0)
            vmax = self.settings.getint("max_c", fallback=32)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

            palette_key = self.settings.get("palette", fallback="thermal").lower()
            palettes = {"thermal": "magma", "vivid": "turbo", "deep": "viridis", "ocean": "inferno"}
            cmap = plt.get_cmap(palettes.get(palette_key, "magma"))

            title_text = "Sea Surface Temp (°C)"
            tick_format = '%d'

        # --- Canvas Initialization ---
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Render complete mapped geographic array using exact pixel cell boundaries
        mesh = plot.ax.pcolormesh(
            lons_clipped, lats_clipped, display_data,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            alpha=alpha,
            shading='nearest',
            rasterized=True,
            zorder=2
        )

        # --- Colorbar Overlay ---
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03]
        }
        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor('#111111')
        cbar_ax.patch.set_alpha(0.4)

        calculated_ticks = np.linspace(vmin, vmax, 5)
        cbar = plt.colorbar(mesh, cax=cbar_ax, orientation='horizontal', ticks=calculated_ticks)

        cbar.ax.xaxis.set_tick_params(color='white', labelsize=key_fontsize, labelcolor='white', pad=3)
        cbar.outline.set_edgecolor('white')
        cbar.outline.set_linewidth(0.5)
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter(tick_format))
        cbar.ax.set_title(title_text, color='white', fontsize=key_fontsize, pad=5, weight='bold')

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, 'close', None)
        if callable(plt_close):
            plt_close()

        logger.debug(f"Successfully rendered raw NOAA OISST map in {self.mode} mode.")

    def run(self):
        self.exit_if_disabled()
        # Construct paths and target endpoints using the common base_url
        current_year = datetime.now().year
        if self.mode == "anomaly":
            self.nc_path = os.path.join(self.workdir, "data/noaa_oisst_anomaly.nc")
            self.target_url = f"{self.base_url}/sst.day.anom.{current_year}.nc"
        else:
            self.nc_path = os.path.join(self.workdir, "data/noaa_oisst_mean.nc")
            self.target_url = f"{self.base_url}/sst.day.mean.{current_year}.nc"

        try:
            data_updated = self.download_data()
            if data_updated or not os.path.exists(self.output_path) or self.config.has_changed:
                self.plot()
        except Exception as e:
            logger.exception(f"SST update execution failure: {e}")