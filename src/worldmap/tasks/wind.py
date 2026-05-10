#!/usr/bin/env python3
import os
import logging
import warnings
import requests
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
        """Renders the wind vector transparent PNG with configurable density."""
        logger.debug(f"Plotting wind vectors to {self.output_path}...")

        # Configuration & Geometry Setup
        density = self.settings.getint("density", fallback=12)
        vector_color = self.settings.get("vector_color", fallback="cyan")
        barb_length = self.settings.getint("barb_length", fallback=5)

        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        # Load the GRIB Data
        # We filter for 10m heightAboveGround (Standard meteorological surface wind)
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}
            },
        )

        bbox = self.map_region_bbox

        # Handle Longitude Shifting (Prime Meridian vs Date Line)
        if bbox:
            if bbox[0] < 0:
                # Shift from 0..360 to -180..180
                ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
                ds = ds.sortby('longitude')
            elif bbox[2] > 180.0:
                # Cap eastern edge for non-Date-Line crossing maps
                bbox[2] = 180.0

        # Extract and Subsample (Thin) Data
        # We slice the arrays using the density step to prevent clutter
        u = ds["u10"].values
        v = ds["v10"].values
        lons, lats = ds["longitude"].values, ds["latitude"].values

        lons_thin = lons[::density]
        lats_thin = lats[::density]
        # Multi-dimensional slicing for the U and V grids
        u_thin = u[::density, ::density]
        v_thin = v[::density, ::density]

        # Initialize Matplotlib Figure
        if bbox:
            width_deg = bbox[2] - bbox[0]
            height_deg = bbox[3] - bbox[1]
            aspect = width_deg / height_deg
            # Ensure the plot height matches the image aspect ratio
            fig = plt.figure(figsize=(plot_target_width, plot_target_width / aspect), dpi=100)
        else:
            fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        ax = plt.axes(projection=ccrs.PlateCarree())

        # Set Geographic Extent
        if bbox:
            logger.debug(f"Setting wind extent to bbox: {bbox}")
            ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        else:
            logger.debug("Setting global wind extent")
            ax.set_global()

        # Plot Wind Barbs
        # These standard barbs indicate speed (half-line = 5kts, full = 10kts, flag = 50kts)
        ax.barbs(
            lons_thin, lats_thin, u_thin, v_thin,
            length=barb_length,
            linewidth=0.6,
            color=vector_color,
            transform=ccrs.PlateCarree()
        )

        # Set Transparency and Final Save
        # Remove all borders, axes, and background colors
        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        # Save as transparent PNG for the compositor
        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)
        logger.debug(f"Wind vector plot saved successfully.")

    def run(self):
        """Entry point for the task."""
        self.exit_if_disabled()
        try:
            url, date, run = self.find_latest_gfs_file()
            logger.debug(f"Using GFS run: {date} {run}Z")
            self.download_data(url)
            self.plot()
            logger.debug("Wind update complete.")
        finally:
            if os.path.exists(self.grib_path):
                os.remove(self.grib_path)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = WorldMapConfig(args.config)
    updater = WindUpdater(config, None)
    updater.run()


if __name__ == "__main__":
    main()