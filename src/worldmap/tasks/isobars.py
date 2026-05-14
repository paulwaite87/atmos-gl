#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
import scipy.ndimage as ndimage
from datetime import datetime, timedelta, timezone
from matplotlib import patheffects
from typing import cast

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

# Silence warnings from GRIB backend
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
logging.getLogger("gribapi.bindings").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)
        self.set_output_path()
        self.grib_path = os.path.join(self.workdir, "data/gfs_isobars.grib2")

    def check_remote_freshness(self):
        """Finds the most recent GFS run and checks if it's newer than local cache."""
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                url = f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000"
                try:
                    # GFS on NOMADS supports HEAD requests for freshness checks
                    response = requests.head(url, timeout=10)
                    if response.status_code == 200:
                        remote_mtime_str = response.headers.get('Last-Modified')
                        if remote_mtime_str:
                            remote_mtime = datetime.strptime(remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z').replace(
                                tzinfo=timezone.utc)

                            if os.path.exists(self.grib_path):
                                local_mtime = datetime.fromtimestamp(os.path.getmtime(self.grib_path), tz=timezone.utc)
                                if remote_mtime <= local_mtime:
                                    return url, False

                        return url, True
                except requests.RequestException:
                    continue

        if os.path.exists(self.grib_path):
            return None, False
        raise RuntimeError("Could not find recent GFS isobar data on NOMADS.")

    def _get_mslp_range(self, grib_url):
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        for i, line in enumerate(lines):
            if ":PRMSL:mean sea level:" in line:
                start = int(line.split(":")[1])
                # End is the start of the next record minus 1
                end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
                return start, end
        raise RuntimeError("PRMSL field not found in GFS index.")

    def download_data(self, url):
        """Downloads only the MSLP portion via byte-range."""
        start, end = self._get_mslp_range(url)
        headers = {"Range": f"bytes={start}-{end}"}
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders the isobar transparent PNG with registration fixes."""
        logger.debug(f"Plotting isobars to {self.output_path}")

        # Matches sst.py target calculations
        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100
        bbox = self.map_region_bbox

        # Load GRIB data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}},
        )

        # Convert Pa to hPa and smooth
        p = ds["prmsl"].values / 100.0
        lons, lats = ds["longitude"].values, ds["latitude"].values

        # Standardize longitudes to -180..180 to match bbox logic
        lons = ((lons + 180) % 360) - 180
        lon_idx = np.argsort(lons)
        lons = lons[lon_idx]
        p = p[:, lon_idx]

        p_smooth = ndimage.gaussian_filter(p, sigma=1.2)

        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Contour styling
        step = self.settings.getint("isobar_step", fallback=4)
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", fallback="white")
        f_size = self.settings.getint("label_fontsize", fallback=10)

        # High-contrast effects for visibility over dark ocean
        line_effect = [patheffects.withStroke(linewidth=2.0, foreground="black", alpha=0.4)]

        cs = plot.ax.contour(
            lons, lats, p_smooth,
            levels=levels,
            colors=color,
            linewidths=1.0,
            transform=ccrs.PlateCarree()
        )

        # Apply effects to lines
        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(line_effect)

        # Labels
        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels:
            for txt in labels:
                txt.set_path_effects(line_effect)

        plot.save_figure(self.output_path)
        ds.close()

    def run(self):
        self.exit_if_disabled()
        try:
            url, needs_download = self.check_remote_freshness()
            if needs_download:
                logger.info(f"Downloading fresh isobar data...")
                self.download_data(url)

            if needs_download or not os.path.exists(self.output_path) or self.config.has_changed:
                logger.info("Generating Isobar plot...")
                self.plot()
        except Exception as e:
            logger.error(f"Isobar update failed: {e}")
