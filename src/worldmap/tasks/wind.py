#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from datetime import datetime, timedelta, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
gribapi_logger = logging.getLogger("gribapi.bindings")
gribapi_logger.setLevel(logging.ERROR)
gribapi_logger.propagate = False

logger = logging.getLogger(__name__)


class WindUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Wind", map_data)
        self.set_output_path()
        self.grib_path = os.path.join(self.workdir, "data/gfs_wind_temp.grib2")

    def find_latest_gfs_file(self):
        """Finds the most recent GFS run on NOAA NOMADS."""
        base_url = self.settings.get("url")
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                url = f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000"
                try:
                    r = requests.head(url, timeout=10)
                    if r.status_code == 200:
                        return url, date_str, run
                except requests.RequestException:
                    continue
        raise RuntimeError("Could not find a recent GFS file on NOMADS.")

    def _get_wind_range(self, grib_url):
        """Parse .idx file to find the byte range for 10m U and V wind components."""
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")

        u_start = v_start = end_byte = None

        # UGRD and VGRD at 10m are almost always contiguous in GFS.
        for i, line in enumerate(lines):
            if ":UGRD:10 m above ground:" in line:
                u_start = int(line.split(":")[1])
            elif ":VGRD:10 m above ground:" in line:
                v_start = int(line.split(":")[1])
                # The end of VGRD is the start of the next variable
                end_byte = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
                break

        if u_start is not None and end_byte is not None:
            # Return the block that covers both U and V
            return min(u_start, v_start), end_byte

        raise RuntimeError("10m Wind fields not found in GFS index.")

    def download_data(self, url):
        """Downloads only the U and V wind portion of the GRIB2."""
        start, end = self._get_wind_range(url)
        headers = {"Range": f"bytes={start}-{end}"}

        logger.debug("Downloading Wind data from GFS...")
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders wind vectors with auto-calculated spacing based on zoom level."""
        logger.debug(f"Plotting wind vectors to {self.output_path}...")

        # Configuration & Bounding Box
        bbox = self.map_region_bbox
        vector_color = self.settings.get("vector_color", fallback="cyan")
        plot_target_width = float(self.target_width) / 100

        base_len = self.settings.getfloat("barb_length_base", fallback=5.0)
        len_step = self.settings.getfloat("barb_length_step", fallback=1.0)

        # Dynamic spacing calculation
        if bbox:
            lon_span = abs(bbox[2] - bbox[0])
            # Divide by number of barbs across the image width
            calc_spacing = lon_span / self.settings.getfloat("barb_density", fallback=30.0)
            # Clamp to GFS resolution (0.25) as minimum, and some reasonable max
            spacing_deg = max(0.25, calc_spacing)
        else:
            # Fallback for global if no bbox
            spacing_deg = 5.0

        # Convert degrees to array index steps (GFS is 0.25 deg resolution)
        density_step = max(1, int(spacing_deg / 0.25))

        logger.debug(f"Auto-calculated spacing: {spacing_deg:.2f}° (Step: {density_step})")

        # Load Data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}},
        )

        # Handle Longitude Shifting
        if bbox:
            if bbox[0] < 0:
                ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
                ds = ds.sortby('longitude')
            elif bbox[2] > 180.0:
                bbox[2] = 180.0

        # Extract and Flatten
        u = ds["u10"].values.squeeze()
        v = ds["v10"].values.squeeze()
        lons = ds["longitude"].values
        lats = ds["latitude"].values

        lon2d, lat2d = np.meshgrid(lons, lats)

        lons_flat = lon2d[::density_step, ::density_step].flatten()
        lats_flat = lat2d[::density_step, ::density_step].flatten()
        u_flat = u[::density_step, ::density_step].flatten()
        v_flat = v[::density_step, ::density_step].flatten()

        # Speed Calculation (m/s to kph)
        speed_kph = np.sqrt(u_flat ** 2 + v_flat ** 2) * 3.6

        # Setup Figure
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

        # Plot in Speed Bins
        speed_bins = [
            (0, 20, base_len),
            (20, 40, base_len + len_step),
            (40, 60, base_len + len_step * 2),
            (60, 80, base_len + len_step * 3),
            (80, 999, base_len + len_step * 4)
        ]

        for s_min, s_max, current_length in speed_bins:
            mask = (speed_kph >= s_min) & (speed_kph < s_max)
            if not np.any(mask):
                continue

            ax.barbs(
                lons_flat[mask], lats_flat[mask], u_flat[mask], v_flat[mask],
                length=current_length,
                linewidth=0.6,
                color=vector_color,
                transform=ccrs.PlateCarree()
            )

        # Save
        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)

    def run(self):
        self.exit_if_disabled()
        try:
            url, date, run = self.find_latest_gfs_file()
            self.download_data(url)
            self.plot()
        finally:
            if os.path.exists(self.grib_path):
                os.remove(self.grib_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = WorldMapConfig(args.config)
    updater = WindUpdater(config, None)
    updater.run()