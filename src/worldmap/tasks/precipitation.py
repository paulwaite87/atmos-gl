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
import cartopy.mpl.geoaxes as geoaxes
from datetime import datetime, timedelta, timezone
from typing import cast

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PrecipitationUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Precipitation", map_data)
        self.set_output_path()
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
        """Finds the most recent GFS run and checks if it's newer than local cache."""
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
        raise RuntimeError("Could not find GFS data on NOMADS.")

    def _get_precip_range(self, grib_url):
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        for i, line in enumerate(lines):
            if ":PRATE:surface:" in line:
                start_byte = int(line.split(":")[1])
                end_byte = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
                return start_byte, end_byte
        raise RuntimeError("PRATE (Precipitation) not found in GFS index.")

    def download_data(self, url):
        """Performs a partial byte-range download of the PRATE layer."""
        start, end = self._get_precip_range(url)
        headers = {"Range": f"bytes={start}-{end}"}

        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders the precipitation transparent PNG with alignment fixes."""
        logger.debug(f"Plotting precipitation to {self.output_path}")

        min_rate = self.settings.getfloat("min_mm_hr", fallback=0.1)
        alpha = self.settings.getfloat("alpha", fallback=0.5)
        palette_name = self.settings.get("palette", fallback="standard")

        bbox = self.map_region_bbox
        plot_target_width = float(self.target_width) / 100
        plot_target_height = float(self.target_height) / 100

        ds = xr.open_dataset(self.grib_path, engine="cfgrib")
        prate = ds["prate"].values.squeeze() * 3600.0  # kg/m^2/s to mm/hr
        lons, lats = ds.longitude.values, ds.latitude.values

        # Standardize longitudes to -180..180 for reliable bbox clipping
        lons = ((lons + 180) % 360) - 180
        idx = np.argsort(lons)
        lons, prate = lons[idx], prate[:, idx]

        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Colormap setup
        levels = [min_rate, 0.5, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0]
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        rgba_colors = [(*c, alpha) for c in base_colors]

        cmap = mcolors.ListedColormap(rgba_colors)
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        plot.ax.contourf(lons, lats, prate,
                    levels=levels,
                    cmap=cmap,
                    norm=norm,
                    transform=ccrs.PlateCarree(),
                    extend='max')

        plot.save_figure(self.output_path)
        ds.close()

    def run(self):
        self.exit_if_disabled()
        try:
            url, needs_download = self.check_remote_freshness()
            if needs_download:
                logger.info("Downloading fresh precipitation data...")
                self.download_data(url)

            if needs_download or not os.path.exists(self.output_path) or self.config.has_changed:
                logger.info("Generating Precipitation plot...")
                self.plot()
        except Exception as e:
            logger.error(f"Precipitation update failed: {e}")
