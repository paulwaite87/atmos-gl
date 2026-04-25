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
from matplotlib import patheffects

# Internal imports from your new library
from worldmap.lib.config import WorldMapConfig

# Silence specific data-processing warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class IsobarUpdater:
    def __init__(self, config: WorldMapConfig):
        self.config = config
        self.settings = config.get_section("isobars")
        self.common = config.get_section("common")

        # Path resolution using the workdir from config
        self.workdir = self.common.get("workdir", ".")
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

        logger.info("Downloading MSLP data from GFS...")
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders the isobar transparent PNG."""
        logger.info(f"Plotting isobars to {self.output_path}...")
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
            },
        )

        p = ds["prmsl"].values / 100.0
        lons, lats = ds["longitude"].values, ds["latitude"].values

        fig = plt.figure(figsize=(20.48, 10.24), dpi=100)
        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_global()

        levels = np.arange(940, 1060, 4)
        color = self.settings.get("isobar_color", fallback="white")
        f_size = self.settings.getint("label_fontsize", fallback=10)

        effect = [patheffects.withStroke(linewidth=2.0, foreground="black", alpha=0.3)]

        cs = ax.contour(
            lons,
            lats,
            p,
            levels=levels,
            colors=color,
            linewidths=1.0,
            transform=ccrs.PlateCarree(),
        )

        # Apply path effects to contours
        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(effect)

        # Draw and style labels
        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels and self.settings.getboolean("label_outline", fallback=False):
            for txt in labels:
                txt.set_path_effects(effect)

        # Transparency formatting for compositing
        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)

    def run(self):
        """Entry point for the task."""
        if not self.settings.getboolean("enabled", fallback=False):
            logger.info("Isobars task disabled. Skipping.")
            return

        try:
            url, date, run = self.find_latest_gfs_file()
            logger.info(f"Using GFS run: {date} {run}Z")
            self.download_data(url)
            self.plot()
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
