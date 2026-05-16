#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from datetime import datetime, timedelta, timezone
import gc

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class TemperatureUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Temperature", map_data)
        self.set_output_path()
        self.grib_path = os.path.join(self.workdir, "data/gfs_temp.grib2")

        # DESIGNED GRADIENTS FOR AIR TEMPERATURE (-40C to +45C)
        self.PALETTES = {
            "global_thermal": [
                (0.2, 0.0, 0.4),  # -40C: Deep Violet
                (0.0, 0.2, 0.6),  # -20C: Navy Blue
                (0.0, 0.8, 1.0),  # -5C: Frost Cyan
                (1.0, 1.0, 1.0),  # 0C: Freezing White
                (1.0, 0.9, 0.2),  # 15C: Pleasant Yellow
                (1.0, 0.4, 0.0),  # 30C: Hot Orange
                (0.6, 0.0, 0.1)   # 45C: Searing Crimson
            ],
            "extreme_contrast": [
                (0.7, 0.0, 0.7),  # -40C: Intense Magenta
                (0.0, 0.2, 1.0),  # -20C: Electric Blue
                (0.0, 0.9, 1.0),  # -5C: Bright Cyan
                (0.0, 0.9, 0.0),  # 5C: Vivid Neon Green
                (1.0, 1.0, 0.0),  # 18C: Blazing Yellow
                (1.0, 0.5, 0.0),  # 30C: Safety Orange
                (1.0, 0.0, 0.0),  # 38C: Pure Red
                (0.9, 0.7, 1.0)   # 45C: White-Hot Purple
            ],
            "twilight_gradient": [
                (0.1, 0.1, 0.3),  # -40C: Dark Indigo
                (0.2, 0.4, 0.6),  # -20C: Muted Steel Blue
                (0.5, 0.7, 0.7),  # 0C: Slate
                (0.8, 0.7, 0.5),  # 15C: Warm Sand
                (0.8, 0.4, 0.3),  # 30C: Burnt Terracotta
                (0.5, 0.1, 0.1)   # 45C: Deep Brick
            ]
        }

    def check_remote_freshness(self):
        """Finds the most recent available GFS Atmospheric GRIB2 cycle run on NOMADS."""
        base_url = self.settings.get("url").rstrip('/')
        forecast_hour = self.settings.get("forecast_hour", fallback="024").zfill(3)
        now = datetime.now(timezone.utc)

        cycles_to_try = ["18", "12", "06", "00"]

        for day_offset in range(4):
            date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")

            for cycle in cycles_to_try:
                if day_offset == 0 and int(cycle) > now.hour:
                    continue

                url = f"{base_url}/gfs.{date_str}/{cycle}/atmos/gfs.t{cycle}z.pgrb2.0p25.f{forecast_hour}"

                try:
                    logger.debug(f"Probing GFS-Atmos availability: {date_str} Cycle {cycle}z...")
                    response = requests.head(url, timeout=7)

                    if response.status_code == 200:
                        remote_mtime_str = response.headers.get('Last-Modified')
                        if remote_mtime_str:
                            remote_mtime = datetime.strptime(
                                remote_mtime_str, '%a, %d %b %Y %H:%M:%S %Z'
                            ).replace(tzinfo=timezone.utc)

                            if os.path.exists(self.grib_path):
                                local_mtime = datetime.fromtimestamp(os.path.getmtime(self.grib_path), tz=timezone.utc)
                                if remote_mtime <= local_mtime:
                                    logger.info(f"Local temperature cache is fresh ({date_str} {cycle}z).")
                                    return url, False

                        logger.info(f"Found newer atmospheric dataset: {date_str} Run {cycle}z.")
                        return url, True

                except requests.RequestException:
                    continue

        if os.path.exists(self.grib_path):
            logger.warning("Could not contact NOMADS for updates, reverting to existing local file.")
            return None, False

        raise RuntimeError("Critical: Could not locate any valid GFS Atmospheric cycles on NOMADS.")

    def download_data(self, url):
        """Downloads the GRIB2 file via streaming chunks."""
        idx_path = f"{self.grib_path}.idx"
        if os.path.exists(idx_path):
            try:
                os.remove(idx_path)
            except OSError:
                pass

        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.grib_path), exist_ok=True)
        with open(self.grib_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def plot(self):
        """Plots an underlying 2-meter surface temperature heatmap
        with optional isotherm contour lines and an overlay color key.
        """
        import matplotlib.pyplot as plt
        from scipy.interpolate import griddata

        logger.debug(f"Plotting Temperature Data for {self.map_data.region.region_identifier}")

        palette_name = self.settings.get("palette", fallback="global_thermal")
        if palette_name not in self.PALETTES:
            palette_name = "global_thermal"

        alpha_setting = self.settings.getfloat("alpha", fallback=0.75)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        show_freezing_line = self.settings.getboolean("show_freezing_line", fallback=True)

        # Key style configurations
        key_position = self.settings.get("key_position", fallback="bottom-right").strip().lower()
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)

        # Open Dataset with cfgrib filtering for 2-meter above ground level
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={'filter_by_keys': {'typeOfLevel': 'heightAboveGround', 'level': 2}}
        )

        lon_raw = ((ds["longitude"].values + 180) % 360) - 180
        lat_raw = ds["latitude"].values
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        lon_inside = (lon_raw >= lon_min - buf) & (lon_raw <= lon_max + buf)
        lat_inside = (lat_raw >= lat_min - buf) & (lat_raw <= lat_max + buf)

        temp_key = "t2m" if "t2m" in ds else "2t"

        if lon_raw.ndim == 1 and lat_raw.ndim == 1:
            spatial_mask = lat_inside[:, np.newaxis] & lon_inside[np.newaxis, :]
            mesh_lon_raw, mesh_lat_raw = np.meshgrid(lon_raw, lat_raw)

            temp_raw_k = ds[temp_key].values[spatial_mask]
            lons_clipped = mesh_lon_raw[spatial_mask]
            lats_clipped = mesh_lat_raw[spatial_mask]
        else:
            mask = lon_inside & lat_inside
            temp_raw_k = ds[temp_key].values[mask]
            lons_clipped = lon_raw[mask]
            lats_clipped = lat_raw[mask]

        ds.close()
        del ds
        gc.collect()

        valid = ~np.isnan(temp_raw_k)
        if not np.any(valid):
            logger.warning("No temperature coordinates found within the region slice.")
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        temp_raw_c = temp_raw_k[valid] - 273.15

        # 3. Build unified processing grid mesh
        grid_lon = np.linspace(lon_min, lon_max, 300)
        grid_lat = np.linspace(lat_min, lat_max, 300)
        mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

        temp_grid = griddata(points, temp_raw_c, (mesh_lon, mesh_lat), method='linear', fill_value=np.nan)

        # 4. Initialize Core Canvas
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # 5. Render Temperature Contour Heatmap
        custom_rgba_list = [(r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]]
        cmap = mcolors.LinearSegmentedColormap.from_list("surface_temp", custom_rgba_list, N=256)

        levels = np.linspace(-40.0, 45.0, 86)
        norm = mcolors.Normalize(vmin=-40.0, vmax=45.0)

        cf = plot.ax.contourf(
            grid_lon, grid_lat, temp_grid,
            levels=levels,
            cmap=cmap,
            norm=norm,
            extend='both',
            antialiased=True,
            transform=ccrs.PlateCarree(),
            zorder=2
        )

        # 6. Render Freezing Line Isotherm
        if show_freezing_line:
            plot.ax.contour(
                grid_lon, grid_lat, temp_grid,
                levels=[0.0],
                colors=['#00FFFF'],
                linewidths=[1.8],
                linestyles=['dashed'],
                alpha=0.9,
                transform=ccrs.PlateCarree(),
                zorder=4
            )

        # 7. ENHANCEMENT: DYNAMIC ADJUSTED COLOR KEY OVERLAY
        # Format: [left_x, bottom_y, width, height]
        # Uses split-difference modifications to preserve labels and viewport clearance bounds
        position_map = {
            "top-left":     [0.04, 0.89, 0.28, 0.03],
            "top-right":    [0.68, 0.89, 0.28, 0.03],
            "bottom-left":  [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03]
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        # Apply slight translucent backing plate behind the color key for high visibility
        cbar_ax.patch.set_facecolor('#111111')
        cbar_ax.patch.set_alpha(0.4)

        cbar = plt.colorbar(
            cf,
            cax=cbar_ax,
            orientation='horizontal',
            ticks=[-40, -20, 0, 15, 30, 45]
        )

        # Style the color scale numbers and labels beautifully using padded alignments
        cbar.ax.xaxis.set_tick_params(color='white', labelsize=key_fontsize, labelcolor='white', pad=3)
        cbar.outline.set_edgecolor('white')
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title("Temperature (°C)", color='white', fontsize=key_fontsize, pad=5, weight='bold')

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, 'close', None)
        if callable(plt_close):
            plt_close()

        logger.debug("Temperature plotting sequence completed successfully.")

    def run(self):
        self.exit_if_disabled()
        try:
            url, needs_download = self.check_remote_freshness()
            if needs_download:
                logger.info(f"Downloading active GFS-Atmos temp data from {url}...")
                self.download_data(url)

            if needs_download or not os.path.exists(self.output_path) or self.config.has_changed:
                logger.info("Generating Surface Temperature layer...")
                self.plot()
        except Exception as e:
            logger.exception(f"Temperature layer update encountered an error: {e}")