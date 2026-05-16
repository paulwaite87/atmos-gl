#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.ndimage import gaussian_filter
from datetime import datetime, timedelta, timezone

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
            "standard": [
                (0.0, 1.0, 1.0),
                (0.0, 0.5, 1.0),
                (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.5, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0)
            ],
            "ocean_blue": [
                (0.8, 0.9, 1.0),
                (0.6, 0.8, 1.0),
                (0.4, 0.6, 1.0),
                (0.2, 0.4, 1.0),
                (0.0, 0.2, 0.8),
                (0.0, 0.0, 0.6),
                (0.0, 0.0, 0.4)
            ],
            "high_contrast": [
                (0.0, 0.9, 0.0),
                (0.0, 0.6, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.6, 0.0),
                (1.0, 0.0, 0.0),
                (0.7, 0.0, 0.0),
                (1.0, 0.0, 1.0)
            ]
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
        """Renders precipitation with early clipping to prevent memory exhaustion."""
        import matplotlib.pyplot as plt
        from scipy.interpolate import RegularGridInterpolator
        import gc  # Garbage collector

        logger.debug(f"Plotting precipitation for {self.map_data.region.region_identifier}")

        min_rate = self.settings.getfloat("min_mm_hr", fallback=0.1)
        alpha = self.settings.getfloat("alpha", fallback=0.5)
        palette_name = self.settings.get("palette", fallback="standard")

        # Parse key layout configurations
        key_position = self.settings.get("key_position", fallback="bottom-right").strip().lower()
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)

        # 1. Load Dataset and Clip Immediately
        ds = xr.open_dataset(self.grib_path, engine="cfgrib")

        # Standardize longitudes to -180..180
        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
        ds = ds.sortby("longitude")

        # Define BBox with a small buffer for smooth edges
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        # SLICE EARLY: This is the primary memory-saving step
        ds_clipped = ds.sel(
            latitude=slice(lat_max + buf, lat_min - buf),
            longitude=slice(lon_min - buf, lon_max + buf)
        )

        prate = ds_clipped["prate"].values.squeeze() * 3600.0
        lons = ds_clipped.longitude.values
        lats = ds_clipped.latitude.values

        # Explicit cleanup of the large dataset
        ds.close()
        del ds
        gc.collect()

        # 2. Interpolation on the clipped subset only
        step = 0.02
        new_lats = np.arange(lats.min(), lats.max() + step, step)
        new_lons = np.arange(lons.min(), lons.max() + step, step)

        # Handle latitude ordering for Interpolator (must be strictly increasing)
        if lats[0] > lats[-1]:
            lats_inc, prate_inc = lats[::-1], prate[::-1, :]
        else:
            lats_inc, prate_inc = lats, prate

        fn = RegularGridInterpolator(
            (lats_inc, lons),
            prate_inc,
            bounds_error=False,
            fill_value=0
        )

        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing='ij')
        prate_smooth = fn((mesh_lats, mesh_lons))

        # 3. Setup Plotting
        plot = Plot(self.map_data.region)
        plot.get_figure()

        levels = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0]
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        rgba_colors = [(*c, alpha) for c in base_colors]

        cmap = mcolors.LinearSegmentedColormap.from_list("smooth_precip", rgba_colors, N=256)
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        # 4. Render Heatmap Contour
        prate_smooth = gaussian_filter(prate_smooth, sigma=1.2)
        cf = plot.ax.contourf(
            new_lons, new_lats, prate_smooth,
            levels=levels,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend='max',
            antialiased=True,
            zorder=2
        )

        # 5. ENHANCEMENT: DYNAMIC ADJUSTED COLOR KEY OVERLAY
        position_map = {
            "top-left":     [0.04, 0.89, 0.28, 0.03],
            "top-right":    [0.68, 0.89, 0.28, 0.03],
            "bottom-left":  [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03]
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor('#111111')
        cbar_ax.patch.set_alpha(0.4)

        # Clean selection of key checkpoints from the BoundaryNorm spectrum
        key_ticks = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]

        cbar = plt.colorbar(
            cf,
            cax=cbar_ax,
            orientation='horizontal',
            ticks=key_ticks
        )

        cbar.ax.xaxis.set_tick_params(color='white', labelsize=key_fontsize, labelcolor='white', pad=3)
        cbar.outline.set_edgecolor('white')
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title("Precipitation (mm/hr)", color='white', fontsize=key_fontsize, pad=5, weight='bold')

        plot.save_figure(self.output_path)

        # Final cleanup
        plt_close = getattr(plot, 'close', None)
        if callable(plt_close):
            plt_close()

        logger.debug(f"Finished Precipitation plot. Memory cleared.")

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