#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import scipy.ndimage as ndimage
from datetime import datetime, timedelta, timezone
from matplotlib import patheffects

# Internal imports from your new library
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

# Silence specific data-processing warnings and talkative libraries
warnings.filterwarnings("ignore", message=".*missingValue.*")

# This silences the cfgrib high-level logger
logging.getLogger("cfgrib").setLevel(logging.ERROR)

# This silences the underlying ecCodes bindings
gribapi_logger = logging.getLogger("gribapi.bindings")
gribapi_logger.setLevel(logging.ERROR)
gribapi_logger.propagate = False

logger = logging.getLogger(__name__)


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)

        # Path resolution using the workdir from config
        self.grib_path = os.path.join(self.workdir, "data/gfs_temp.grib2")
        self.output_path = os.path.join(self.workdir, self.settings.get("outfile"))

    def find_latest_gfs_file(self):
        """Finds the most recent GFS run on NOAA NOMADS."""
        base_url = self.settings.get("url")
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                url = (
                    f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000"
                )
                try:
                    r = requests.head(url, timeout=10)
                    if r.status_code == 200:
                        return url, date_str, run
                except requests.RequestException:
                    continue
        raise RuntimeError("Could not find a recent GFS file on NOMADS.")

    def _get_mslp_range(self, grib_url):
        """Parse .idx file for partial download."""
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")

        for i, line in enumerate(lines):
            if ":PRMSL:mean sea level:" in line:
                start = int(line.split(":")[1])
                end = (
                    int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
                )
                return start, end
        raise RuntimeError("PRMSL field not found in GFS index.")

    def download_data(self, url):
        """Downloads only the MSLP portion of the GRIB2."""
        start, end = self._get_mslp_range(url)
        headers = {"Range": f"bytes={start}-{end if end else ''}"}

        logger.debug("Downloading MSLP data from GFS...")
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders the isobar transparent PNG, perfectly scaled and smoothed."""
        logger.debug(f"Plotting isobars to {self.output_path}...")

        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        # Load the Data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
            },
        )

        # This gets the 'region' setting from [xplanet] as a bbox or
        # None if it isn't defined, which represents 'The World'
        bbox = self.map_region_bbox

        # Smart Longitude Handling
        # GFS is natively 0 to 360.
        # If the user's bbox crosses the Prime Meridian (e.g. -10 to 20), we must shift it to -180 to 180.
        # If it crosses the Date Line (e.g. 150 to 190), 0 to 360 works perfectly natively!
        if bbox:
            if bbox[0] < 0:
                logger.debug("Shifting GFS longitudes to -180..180 for Western Hemisphere")
                ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
                ds = ds.sortby('longitude')
            elif bbox[2] > 180.0:
                logger.debug("Cropping East longitude to 180")
                bbox[2] = 180.0

        p = ds["prmsl"].values / 100.0
        lons, lats = ds["longitude"].values, ds["latitude"].values

        # Smooth the grid for high-resolution zoom
        # This prevents the lines from looking "chunky" or polygonal when zoomed in
        p_smooth = ndimage.gaussian_filter(p, sigma=1.2)

        # Canvas sizing
        if bbox:
            # Calculate aspect ratio to prevent padding/stretching
            width_deg = bbox[2] - bbox[0]
            height_deg = bbox[3] - bbox[1]
            aspect = width_deg / height_deg
            # Target width is 2048, calculate precise height
            fig = plt.figure(figsize=(plot_target_width, plot_target_width / aspect), dpi=100)
        else:
            fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        ax = plt.axes(projection=ccrs.PlateCarree())

        # Set Extent
        if bbox:
            logger.debug(f"Setting bbox isobars extent: {bbox}")
            ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        else:
            logger.debug("Setting global isobars extent")
            ax.set_global()

        # Adaptive contour density
        step = 2 if bbox else 4
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", fallback="white")
        f_size = self.settings.getint("label_fontsize", fallback=10)
        effect = [patheffects.withStroke(linewidth=2.0, foreground="black", alpha=0.3)]

        # Draw the smooth contours
        cs = ax.contour(
            lons,
            lats,
            p_smooth,  # Using the smoothed data array
            levels=levels,
            colors=color,
            linewidths=1.2,  # Slightly thicker looks better on smooth lines
            transform=ccrs.PlateCarree(),
        )

        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(effect)

        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels and self.settings.getboolean("label_outline", fallback=False):
            for txt in labels:
                txt.set_path_effects(effect)

        # Transparency and Output
        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)

    def run(self):
        """Entry point for the task."""
        self.exit_if_disabled()

        try:
            url, date, run = self.find_latest_gfs_file()
            logger.debug(f"Using GFS run: {date} {run}Z")
            self.download_data(url)
            self.plot()
            logger.debug("Isobars update complete.")
        finally:
            if os.path.exists(self.grib_path):
                os.remove(self.grib_path)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    # Using your new lib
    config = WorldMapConfig(args.config)
    updater = IsobarUpdater(config)
    updater.run()


if __name__ == "__main__":
    main()
