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

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
logging.getLogger("gribapi.bindings").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)
        self.set_output_path()
        # Path is now persistent
        self.grib_path = os.path.join(self.workdir, "data/gfs_isobars.grib2")

    def check_remote_freshness(self):
        """Finds the most recent GFS run and checks if it's newer than local cache."""
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                # Using f000 for current analysis isobars
                url = f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000"
                try:
                    response = requests.head(url, timeout=10)
                    if response.status_code == 200:
                        remote_mtime_str = response.headers.get('Last-Modified')
                        remote_mtime = None
                        if remote_mtime_str:
                            remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                                tzinfo=timezone.utc)

                        local_file_exists = os.path.exists(self.grib_path)
                        if local_file_exists and remote_mtime:
                            local_mtime = datetime.fromtimestamp(os.path.getmtime(self.grib_path), tz=timezone.utc)
                            if remote_mtime <= local_mtime:
                                logger.info(f"Isobar cache up to date: {date_str} {run}z")
                                return url, False

                        logger.info(f"New Isobar data found: {date_str} {run}z")
                        return url, True
                except requests.RequestException:
                    continue

        if os.path.exists(self.grib_path):
            return None, False
        raise RuntimeError("Could not find recent GFS isobar data on NOMADS.")

    def _get_mslp_range(self, grib_url):
        """Parse .idx file for partial download of PRMSL layer."""
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")

        for i, line in enumerate(lines):
            if ":PRMSL:mean sea level:" in line:
                start = int(line.split(":")[1])
                end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
                return start, end
        raise RuntimeError("PRMSL field not found in GFS index.")

    def download_data(self, url):
        """Downloads only the MSLP portion via byte-range."""
        start, end = self._get_mslp_range(url)
        headers = {"Range": f"bytes={start}-{end if end else ''}"}

        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders the isobar transparent PNG."""
        logger.debug(f"Plotting isobars to {self.output_path}")

        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}},
        )

        bbox = self.map_region_bbox

        if bbox:
            if bbox[0] < 0:
                ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
                ds = ds.sortby('longitude')
            elif bbox[2] > 180.0:
                bbox[2] = 180.0

        # Convert Pa to hPa/mbar
        p = ds["prmsl"].values / 100.0
        lons, lats = ds["longitude"].values, ds["latitude"].values
        p_smooth = ndimage.gaussian_filter(p, sigma=1.2)

        if bbox:
            width_deg = bbox[2] - bbox[0]
            height_deg = bbox[3] - bbox[1]
            fig = plt.figure(figsize=(plot_target_width, plot_target_width / (width_deg / height_deg)), dpi=100)
        else:
            fig = plt.figure(figsize=(plot_target_width, plot_target_height), dpi=100)

        ax = plt.axes(projection=ccrs.PlateCarree())
        if bbox:
            ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())
        else:
            ax.set_global()

        # Configurable styles
        step = 2 if bbox else 4
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", fallback="white")
        f_size = self.settings.getint("label_fontsize", fallback=10)
        effect = [patheffects.withStroke(linewidth=2.0, foreground="black", alpha=0.3)]

        cs = ax.contour(
            lons, lats, p_smooth,
            levels=levels,
            colors=color,
            linewidths=1.2,
            transform=ccrs.PlateCarree(),
        )

        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(effect)

        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels and self.settings.getboolean("label_outline", fallback=False):
            for txt in labels:
                txt.set_path_effects(effect)

        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)
        ds.close()

    def run(self):
        self.exit_if_disabled()

        url, needs_download = self.check_remote_freshness()

        if needs_download:
            self.download_data(url)

        png_exists = os.path.exists(self.output_path)
        if needs_download or not png_exists or self.config.has_changed:
            logger.info("Generating Isobar plot...")
            self.plot()
        else:
            logger.info("Isobar PNG is up to date. Skipping.")


if __name__ == "__main__":
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = WorldMapConfig(args.config)
    updater = IsobarUpdater(config, None)
    updater.run()