#!/usr/bin/env python3
import os
import logging
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from datetime import datetime, timedelta, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

logger = logging.getLogger(__name__)


class SSTUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "sst", map_data)
        self.set_output_path()
        # Local cache for the subsetted data
        self.nc_path = os.path.join(self.workdir, "data/rtofs_sst_subset.nc")

    def download_data(self):
        """
        Fetches a geographic subset of RTOFS data using HTTP Byte-Range requests.
        Replaces the retired OPeNDAP/DODS service.
        """
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)
        bbox = self.map_region_bbox  # [lon_min, lat_min, lon_max, lat_max]

        # Search the last 3 days for available forecast files
        for i in range(3):
            date_str = (now - timedelta(days=i)).strftime("%Y%m%d")
            url = f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_n000_prog.nc"

            try:
                logger.debug(f"Attempting remote subsetting via byte-range: {url}")

                with xr.open_dataset(url, engine='h5netcdf', chunks={}) as ds:
                    lon_name = 'Longitude' if 'Longitude' in ds.coords else 'lon'
                    lat_name = 'Latitude' if 'Latitude' in ds.coords else 'lat'

                    lons_raw = ds[lon_name].values
                    lats_raw = ds[lat_name].values

                    if lons_raw.ndim > 1: lons_raw = lons_raw[0, :]
                    if lats_raw.ndim > 1: lats_raw = lats_raw[:, 0]

                    lons_norm = ((lons_raw + 180) % 360) - 180

                    lon_mask = (lons_norm >= bbox[0]) & (lons_norm <= bbox[2])
                    lat_mask = (lats_raw >= bbox[1]) & (lats_raw <= bbox[3])

                    lon_indices = np.where(lon_mask)[0]
                    lat_indices = np.where(lat_mask)[0]

                    if len(lon_indices) == 0 or len(lat_indices) == 0:
                        continue

                    sst_var = next(n for n in ['sst', 'sea_surface_temperature', 'temp'] if n in ds)
                    subset = ds[sst_var].isel(
                        MT=0,
                        Y=slice(lat_indices.min(), lat_indices.max() + 1),
                        X=slice(lon_indices.min(), lon_indices.max() + 1)
                    ).compute()

                    os.makedirs(os.path.dirname(self.nc_path), exist_ok=True)
                    subset.to_netcdf(self.nc_path)

                logger.info(f"Retrieved SST subset for {date_str} ({subset.shape})")
                return True

            except Exception as e:
                logger.debug(f"NOMADS access failed for {date_str}: {e}")
                continue

        if not os.path.exists(self.nc_path):
            raise RuntimeError("Could not retrieve SST data from NOMADS HTTPS endpoints.")
        return False

    def plot(self):
        # --- Configuration Parsing ---
        alpha = self.settings.getfloat("alpha", fallback=0.4)
        palette_key = self.settings.get("palette", fallback="thermal").lower()
        vmin, vmax = self.settings.getint("min_c", fallback=0), self.settings.getint("max_c", fallback=32)
        palettes = {"thermal": "magma", "vivid": "turbo", "deep": "viridis", "ocean": "inferno"}
        cmap_name = palettes.get(palette_key, "magma")

        # Layout positioning flags for the visual key scale
        key_position = self.settings.get("key_position", fallback="bottom-right").strip().lower()
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)

        # --- Data Loading ---
        ds = xr.open_dataset(self.nc_path)
        sst_var = list(ds.data_vars)[0]
        sst_raw = ds[sst_var].values.squeeze()
        sst_c = sst_raw - 273.15 if np.nanmax(sst_raw) > 100 else sst_raw

        lons = ds.Longitude.values if 'Longitude' in ds.coords else ds.lon.values
        lats = ds.Latitude.values if 'Latitude' in ds.coords else ds.lat.values

        if lons.ndim > 1: lons = lons[0, :]
        if lats.ndim > 1: lats = lats[:, 0]

        lons = ((lons + 180) % 360) - 180

        lon_idx = np.argsort(lons)
        lat_idx = np.argsort(lats)

        lons = lons[lon_idx]
        lats = lats[lat_idx]
        sst_c = sst_c[:, lon_idx][lat_idx, :]

        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Capture scalar reference for color key allocation
        mesh = plot.ax.pcolormesh(lons, lats, sst_c,
                                  transform=ccrs.PlateCarree(),
                                  cmap=plt.get_cmap(cmap_name),
                                  alpha=alpha,
                                  vmin=vmin, vmax=vmax,
                                  shading='nearest')

        # --- FINE-TUNED INSET COLOR KEY COORDINATES ---
        # Format: [left_x, bottom_y, width, height]
        # Halfway adjustment to maximize map space while saving labels from clipping
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03]
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        # Render clean semi-translucent backplate for contrast
        cbar_ax.patch.set_facecolor('#111111')
        cbar_ax.patch.set_alpha(0.4)

        # Generate dynamically distributed ticks
        calculated_ticks = np.linspace(vmin, vmax, 5)

        cbar = plt.colorbar(
            mesh,
            cax=cbar_ax,
            orientation='horizontal',
            ticks=calculated_ticks
        )

        # Apply clean typography offsets
        cbar.ax.xaxis.set_tick_params(color='white', labelsize=key_fontsize, labelcolor='white', pad=3)
        cbar.outline.set_edgecolor('white')
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title("Sea Surface Temp (°C)", color='white', fontsize=key_fontsize, pad=5, weight='bold')

        plot.save_figure(self.output_path)
        ds.close()
        logger.debug("Finished SST plot...saving")

    def run(self):
        self.exit_if_disabled()
        if self.download_data() or not os.path.exists(self.output_path) or self.config.has_changed:
            logger.info("Generating SST plot...")
            self.plot()