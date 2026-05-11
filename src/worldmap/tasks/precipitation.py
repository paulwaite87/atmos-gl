#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
from datetime import datetime, timedelta, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PrecipitationUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Precipitation", map_data)
        self.set_output_path()
        # Ensure path is consistent
        self.grib_path = os.path.join(self.workdir, "data/gfs_precip.grib2")

        self.PALETTES = {
            "standard": [(0.0, 1.0, 1.0), (0.0, 0.5, 1.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.5, 0.0),
                         (1.0, 0.0, 0.0), (1.0, 0.0, 1.0)],
            "ocean_blue": [(0.8, 0.9, 1.0), (0.6, 0.8, 1.0), (0.4, 0.6, 1.0), (0.2, 0.4, 1.0), (0.0, 0.2, 0.8),
                           (0.0, 0.0, 0.6), (0.0, 0.0, 0.4)],
            "high_contrast": [(0.0, 0.9, 0.0), (0.0, 0.6, 0.0), (1.0, 1.0, 0.0), (1.0, 0.6, 0.0), (1.0, 0.0, 0.0),
                              (0.7, 0.0, 0.0), (1.0, 0.0, 1.0)]
        }

    def check_remote_freshness(self):
        """
        Determines if the remote GFS file is newer than our local cache.
        Returns: (url_to_download, is_newer_boolean)
        """
        base_url = self.settings.get("url").rstrip('/')
        forecast_hour = self.settings.get("forecast_hour", fallback="3").zfill(3)
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                url = f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f{forecast_hour}"
                try:
                    response = requests.head(url, timeout=10)
                    if response.status_code == 200:
                        # Get remote timestamp
                        remote_mtime_str = response.headers.get('Last-Modified')
                        remote_mtime = None
                        if remote_mtime_str:
                            remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                                tzinfo=timezone.utc)

                        # Compare with local file
                        local_file_exists = os.path.exists(self.grib_path)
                        if local_file_exists and remote_mtime:
                            local_mtime = datetime.fromtimestamp(os.path.getmtime(self.grib_path), tz=timezone.utc)
                            if remote_mtime <= local_mtime:
                                logger.info(f"Local GFS cache is up to date: {date_str} {run}z")
                                return url, False

                        logger.info(f"New GFS data found: {date_str} {run}z")
                        return url, True
                except requests.RequestException:
                    continue

        if os.path.exists(self.grib_path):
            return None, False
        raise RuntimeError("Could not find GFS data on NOMADS.")

    def _get_precip_range(self, grib_url):
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        start_byte = end_byte = None
        for i, line in enumerate(lines):
            if ":PRATE:surface:" in line:
                start_byte = int(line.split(":")[1])
                # GRIB indices point to the start of the next record; we subtract 1 for the range
                end_byte = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
                break
        if start_byte is not None:
            return start_byte, end_byte
        raise RuntimeError("PRATE (Precipitation) not found in GFS index.")

    def download_data(self, url):
        """Performs a partial byte-range download of the PRATE layer."""
        start, end = self._get_precip_range(url)
        headers = {"Range": f"bytes={start}-{end}"} if end else {"Range": f"bytes={start}-"}

        logger.debug(f"Downloading partial GRIB range: {headers['Range']}")
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        min_rate = self.settings.getfloat("min_mm_hr", fallback=0.1)
        alpha = self.settings.getfloat("alpha", fallback=0.5)
        palette_name = self.settings.get("palette", fallback="standard")

        bbox = self.map_region_bbox
        plot_target_width = float(self.target_width) / 100

        # Note: cfgrib creates .idx files locally; xr.open_dataset handles this.
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")
        prate = ds["prate"].values.squeeze() * 3600.0  # kg/m^2/s to mm/hr
        lons, lats = ds.longitude.values, ds.latitude.values

        if bbox and bbox[0] < 0:
            lons = ((lons + 180) % 360) - 180
            idx = np.argsort(lons)
            lons, prate = lons[idx], prate[:, idx]

        if bbox:
            width_deg, height_deg = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
            fig = plt.figure(figsize=(plot_target_width, plot_target_width / (width_deg / height_deg)), dpi=100)
        else:
            fig = plt.figure(figsize=(plot_target_width, float(self.target_height) / 100), dpi=100)

        ax = plt.axes(projection=ccrs.PlateCarree())
        if bbox:
            ax.set_extent([bbox[0], bbox[2], bbox[1], bbox[3]], crs=ccrs.PlateCarree())

        levels = [min_rate, 0.5, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0]
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        rgba_colors = [(*c, alpha) for c in base_colors]

        cmap = mcolors.ListedColormap(rgba_colors)
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        ax.contourf(lons, lats, prate, levels=levels, cmap=cmap, norm=norm, transform=ccrs.PlateCarree(), extend='max')

        ax.set_frame_on(False)
        ax.set_position((0, 0, 1, 1))
        ax.patch.set_alpha(0)
        fig.patch.set_alpha(0)
        plt.axis("off")

        plt.savefig(self.output_path, transparent=True, bbox_inches=None, pad_inches=0)
        plt.close(fig)
        ds.close()
        logger.debug(f"Precipitation plot saved to {self.output_path}")

    def run(self):
        self.exit_if_disabled()

        # Check if remote is newer or local cache is missing
        url, needs_download = self.check_remote_freshness()

        if needs_download:
            self.download_data(url)

        # Determine if we need to re-plot
        png_exists = os.path.exists(self.output_path)

        if needs_download or not png_exists or self.config.has_changed:
            logger.info("Generating Precipitation plot...")
            self.plot()
        else:
            logger.info("Precipitation PNG is up to date. Skipping plot.")


if __name__ == "__main__":
    import argparse
    from worldmap.lib.logging import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = WorldMapConfig(args.config)
    updater = PrecipitationUpdater(config, None)
    updater.run()