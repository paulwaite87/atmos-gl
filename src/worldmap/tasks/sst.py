#!/usr/bin/env python3
import os
import logging
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from datetime import datetime, timedelta, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class SSTUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "sst", map_data)
        self.set_output_path()
        # Changed to .nc
        self.nc_path = os.path.join(self.workdir, "data/rtofs_sst.nc")

    def download_data(self):
        """Downloads the RTOFS SST file only if the upstream version is newer."""
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)

        for i in range(3):
            date_str = (now - timedelta(days=i)).strftime("%Y%m%d")
            for mode in ["n000", "f000"]:
                url = f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_{mode}_prog.nc"

                try:
                    logger.debug(f"Checking RTOFS ({mode}) at {url}...")
                    response = requests.head(url, timeout=10)

                    if response.status_code == 200:
                        # Get upstream modification time
                        remote_mtime_str = response.headers.get('Last-Modified')
                        remote_mtime = None
                        if remote_mtime_str:
                            remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                                tzinfo=timezone.utc)

                        # Check local file
                        local_file_exists = os.path.exists(self.nc_path)
                        remote_is_newer = False
                        if local_file_exists and remote_mtime:
                            local_mtime = datetime.fromtimestamp(os.path.getmtime(self.nc_path), tz=timezone.utc)
                            remote_is_newer = remote_mtime > local_mtime

                        # 3. Decision: Download, Use Existing, or Skip
                        if not local_file_exists or remote_is_newer:
                            logger.info(f"Downloading newer SST data: {date_str} ({mode})")
                            r = requests.get(url, stream=True, timeout=120)
                            r.raise_for_status()
                            os.makedirs(os.path.dirname(self.nc_path), exist_ok=True)
                            with open(self.nc_path, "wb") as f:
                                for chunk in r.iter_content(chunk_size=1024 * 1024):
                                    f.write(chunk)
                            return True  # Signal that we downloaded new data
                        else:
                            logger.info("Local SST NetCDF is up to date")
                            return False  # Signal we are using existing data
                except Exception as e:
                    logger.warning(f"Metadata check failed for {url}: {e}")
                    continue

        if not os.path.exists(self.nc_path):
            raise RuntimeError("No local SST data found and could not connect to NOMADS")
        return False

    def plot(self):
        # --- Configuration & Palette Setup ---
        alpha = self.settings.getfloat("alpha", fallback=0.4)
        palette_key = self.settings.get("palette", fallback="thermal").lower()

        # Allows you to "zoom" the color range via config (e.g., 10 to 25 for winter)
        vmin = self.settings.getint("min_c", fallback=0)
        vmax = self.settings.getint("max_c", fallback=32)

        # Professional color mapping dictionary
        palettes = {
            "thermal": "magma",  # Dark, high-tech glow
            "vivid": "turbo",  # High-contrast rainbow
            "deep": "viridis",  # Clean, easy-to-read green/blue
            "ocean": "inferno"  # Firey oranges and blacks
        }

        cmap_name = palettes.get(palette_key, "magma")
        bbox = self.map_region_bbox
        plot_target_width = float(self.target_width) / 100

        logger.debug(f"Plotting SST (Palette: {palette_key}, Alpha: {alpha})")

        # --- Data Loading ---
        ds = xr.open_dataset(self.nc_path)

        # Find the temperature variable
        possible_names = ['sst', 'sea_surface_temperature', 'temp', 't0']
        sst_var = next((name for name in possible_names if name in ds), None)

        if not sst_var:
            available_vars = list(ds.data_vars.keys())
            raise KeyError(f"Could not find temperature variable. Available vars: {available_vars}")

        # Extract and clean data
        sst_raw = ds[sst_var].values.squeeze()

        # Unit conversion
        if np.nanmax(sst_raw) > 100:
            sst_c = sst_raw - 273.15
        else:
            sst_c = sst_raw

        # Coordinate handling
        lons = ds.Longitude.values if 'Longitude' in ds.coords else ds.lon.values
        lats = ds.Latitude.values if 'Latitude' in ds.coords else ds.lat.values

        # Handle International Date Line wrap-around for the Tasman Sea/Pacific
        if bbox and bbox[0] < 0:
            lons = ((lons + 180) % 360) - 180
            idx = np.argsort(lons)
            lons, sst_c = lons[idx], sst_c[:, idx]

        # --- Figure Setup ---
        if bbox:
            width_deg, height_deg = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
            fig = plt.figure(figsize=(plot_target_width, plot_target_width / (width_deg / height_deg)), dpi=100)
        else:
            fig = plt.figure(figsize=(plot_target_width, float(self.target_height) / 100), dpi=100)

        ax = plt.axes(projection=ccrs.PlateCarree())
        if bbox:
            ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        else:
            ax.set_global()

        # --- The Render ---
        mesh = ax.pcolormesh(lons, lats, sst_c,
                             cmap=plt.get_cmap(cmap_name),
                             alpha=alpha,
                             shading='nearest',
                             transform=ccrs.PlateCarree(),
                             vmin=vmin, vmax=vmax)

        # --- Clean up & Save ---
        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)
        ds.close()  # Ensure file handle is closed for persistence logic
        logger.debug(f"SST plot completed using {cmap_name}")

    def run(self):
        """Orchestrates the SST update with bandwidth-saving logic."""
        self.exit_if_disabled()

        # Check if we need to download/update the NetCDF
        new_data_downloaded = self.download_data()

        # Check if the final output PNG already exists
        png_exists = os.path.exists(self.output_path)

        # Only plot if we have new data OR the PNG is missing
        if new_data_downloaded or not png_exists or self.config.has_changed:
            logger.info("Generating SST plot...")
            self.plot()
        else:
            logger.info("SST PNG already exists and data hasn't changed. Skipping plot.")


if __name__ == "__main__":
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    # Note: In standalone mode, MapData might be None;
    # ensure your base classes handle this or provide a mock.
    updater = SSTUpdater(config, None)
    updater.run()