#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
from datetime import datetime, timedelta, timezone

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

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
        # Persist grib path to allow freshness checks
        self.grib_path = os.path.join(self.workdir, "data/gfs_wind.grib2")

    def check_remote_freshness(self):
        """Finds the most recent GFS run and checks if it's newer than local cache."""
        base_url = self.settings.get("url").rstrip('/')
        now = datetime.now(timezone.utc)

        for day_offset in range(3):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
            for run in ["18", "12", "06", "00"]:
                url = f"{base_url}/gfs.{date_str}/{run}/atmos/gfs.t{run}z.pgrb2.0p25.f000"
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
        raise RuntimeError("Could not find a recent GFS file on NOMADS.")

    def _get_wind_range(self, grib_url):
        """Parse .idx file to find the byte range for 10m U and V wind components."""
        r = requests.get(grib_url + ".idx", timeout=30)
        r.raise_for_status()
        lines = r.text.strip().split("\n")

        u_start = v_start = end_byte = None
        for i, line in enumerate(lines):
            if ":UGRD:10 m above ground:" in line:
                u_start = int(line.split(":")[1])
            elif ":VGRD:10 m above ground:" in line:
                v_start = int(line.split(":")[1])
                end_byte = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
                break

        if u_start is not None and v_start is not None:
            return min(u_start, v_start), end_byte
        raise RuntimeError("10m Wind fields not found in GFS index.")

    def download_data(self, url):
        """Downloads only the U and V wind portion of the GRIB2."""
        start, end = self._get_wind_range(url)
        headers = {"Range": f"bytes={start}-{end}"}

        logger.debug(f"Downloading partial Wind GRIB: {headers['Range']}")
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Renders wind vectors with registration and type-hint fixes."""
        logger.debug(f"Plotting wind vectors to {self.output_path}...")

        bbox = self.map_region_bbox
        vector_color = self.settings.get("vector_color", fallback="cyan")
        base_len = self.settings.getfloat("barb_length_base", fallback=5.0)
        len_step = self.settings.getfloat("barb_length_step", fallback=1.0)

        # Spacing logic
        lon_span = abs(bbox[2] - bbox[0]) if bbox else 360
        calc_spacing = lon_span / self.settings.getfloat("barb_density", fallback=30.0)
        spacing_deg = max(0.25, calc_spacing)
        density_step = max(1, int(spacing_deg / 0.25))

        # Load Data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}},
        )

        # Coordinate Standardization
        lons = ds["longitude"].values
        lats = ds["latitude"].values
        u = ds["u10"].values.squeeze()
        v = ds["v10"].values.squeeze()

        # Wrap to -180..180 and sort
        lons = ((lons + 180) % 360) - 180
        idx = np.argsort(lons)
        lons, u, v = lons[idx], u[:, idx], v[:, idx]

        lon2d, lat2d = np.meshgrid(lons, lats)

        # Flatten with calculated density step
        lons_flat = lon2d[::density_step, ::density_step].flatten()
        lats_flat = lat2d[::density_step, ::density_step].flatten()
        u_flat = u[::density_step, ::density_step].flatten()
        v_flat = v[::density_step, ::density_step].flatten()
        speed_kph = np.sqrt(u_flat ** 2 + v_flat ** 2) * 3.6

        # Setup Figure to match target canvas exactly
        plot = Plot(self.map_data.region)
        plot.get_figure()

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

            plot.ax.barbs(
                lons_flat[mask], lats_flat[mask], u_flat[mask], v_flat[mask],
                length=current_length,
                linewidth=0.6,
                color=vector_color,
                transform=ccrs.PlateCarree()
            )

        plot.save_figure(self.output_path)
        ds.close()

    def run(self):
        self.exit_if_disabled()
        try:
            url, needs_download = self.check_remote_freshness()
            if needs_download:
                self.download_data(url)

            if needs_download or not os.path.exists(self.output_path) or self.config.has_changed:
                logger.info("Generating Wind plot...")
                self.plot()
        except Exception as e:
            logger.error(f"Wind update failed: {e}")
