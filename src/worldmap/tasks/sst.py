#!/usr/bin/env python3
import os
import logging
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
from datetime import datetime, timedelta, timezone
from typing import cast

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
        # Ensure your config URL points to the pub/data/nccf/com/rtofs/prod directory
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)
        bbox = self.map_region_bbox  # [lon_min, lat_min, lon_max, lat_max]

        # Search the last 3 days for available forecast files
        for i in range(3):
            date_str = (now - timedelta(days=i)).strftime("%Y%m%d")
            # 'n000' is the analysis/nowcast file
            url = f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_n000_prog.nc"

            try:
                logger.debug(f"Attempting remote subsetting via byte-range: {url}")

                # Open remote dataset lazily. 'chunks={}' enables dask/lazy loading.
                # Requires 'h5netcdf' and 'fsspec' packages.
                with xr.open_dataset(url, engine='h5netcdf', chunks={}) as ds:
                    # Identify coordinate names (RTOFS uses Latitude/Longitude)
                    lon_name = 'Longitude' if 'Longitude' in ds.coords else 'lon'
                    lat_name = 'Latitude' if 'Latitude' in ds.coords else 'lat'

                    # 1. Fetch 1D coordinate vectors to calculate indices
                    lons_raw = ds[lon_name].values
                    lats_raw = ds[lat_name].values

                    # Flatten curvilinear coordinates to 1D for index lookup
                    if lons_raw.ndim > 1: lons_raw = lons_raw[0, :]
                    if lats_raw.ndim > 1: lats_raw = lats_raw[:, 0]

                    # Normalize 0..360 to -180..180 for BBOX comparison
                    lons_norm = ((lons_raw + 180) % 360) - 180

                    # 2. Identify indices matching the BBOX
                    lon_mask = (lons_norm >= bbox[0]) & (lons_norm <= bbox[2])
                    lat_mask = (lats_raw >= bbox[1]) & (lats_raw <= bbox[3])

                    lon_indices = np.where(lon_mask)[0]
                    lat_indices = np.where(lat_mask)[0]

                    if len(lon_indices) == 0 or len(lat_indices) == 0:
                        continue

                    # 3. Trigger the network transfer for only the required slice
                    # RTOFS NetCDF dimensions are usually (MT, Y, X)
                    sst_var = next(n for n in ['sst', 'sea_surface_temperature', 'temp'] if n in ds)
                    subset = ds[sst_var].isel(
                        MT=0,
                        Y=slice(lat_indices.min(), lat_indices.max() + 1),
                        X=slice(lon_indices.min(), lon_indices.max() + 1)
                    ).compute()

                    # Save subset locally to avoid repeated network calls during plotting
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
        # --- Configuration ---
        alpha = self.settings.getfloat("alpha", fallback=0.4)
        palette_key = self.settings.get("palette", fallback="thermal").lower()
        vmin, vmax = self.settings.getint("min_c", fallback=0), self.settings.getint("max_c", fallback=32)
        palettes = {"thermal": "magma", "vivid": "turbo", "deep": "viridis", "ocean": "inferno"}
        cmap_name = palettes.get(palette_key, "magma")

        bbox = self.map_region_bbox
        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        # --- Data Loading ---
        ds = xr.open_dataset(self.nc_path)
        sst_var = list(ds.data_vars)[0]
        sst_raw = ds[sst_var].values.squeeze()
        sst_c = sst_raw - 273.15 if np.nanmax(sst_raw) > 100 else sst_raw

        lons = ds.Longitude.values if 'Longitude' in ds.coords else ds.lon.values
        lats = ds.Latitude.values if 'Latitude' in ds.coords else ds.lat.values

        # Flatten curvilinear coordinates to 1D axes for mapping
        if lons.ndim > 1: lons = lons[0, :]
        if lats.ndim > 1: lats = lats[:, 0]

        # Normalize subset lons back to -180..180
        lons = ((lons + 180) % 360) - 180

        # Sort indices to ensure strictly increasing arrays for pcolormesh
        lon_idx = np.argsort(lons)
        lat_idx = np.argsort(lats)

        lons = lons[lon_idx]
        lats = lats[lat_idx]
        sst_c = sst_c[:, lon_idx][lat_idx, :]

        plot = Plot(self.map_data.region)
        plot.get_figure()

        # This forces the map to respect the non-linear Mercator grid spacing of RTOFS.
        plot.ax.pcolormesh(lons, lats, sst_c,
                      transform=ccrs.PlateCarree(),
                      cmap=plt.get_cmap(cmap_name),
                      alpha=alpha,
                      vmin=vmin, vmax=vmax,
                      shading='nearest')

        plot.save_figure(self.output_path)
        ds.close()
        logger.debug("Finished SST plot...saving")

    def run(self):
        self.exit_if_disabled()
        if self.download_data() or not os.path.exists(self.output_path) or self.config.has_changed:
            logger.info("Generating SST plot...")
            self.plot()
