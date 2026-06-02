#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import cartopy.crs as ccrs

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

    def plot(self):
        """Renders wind vectors with registration and type-hint fixes."""
        logger.debug(f"Plotting wind vectors to {self.output_path}...")

        bbox = self.map_region_bbox
        vector_color = self.settings.get("vector_color", "cyan")
        base_len = self.settings.get("barb_length_base", 5.0)
        len_step = self.settings.get("barb_length_step", 1.0)

        # Spacing logic
        lon_span = abs(bbox[2] - bbox[0]) if bbox else 360
        calc_spacing = lon_span / self.settings.get("barb_density", 30.0)
        spacing_deg = max(0.25, calc_spacing)
        density_step = max(1, int(spacing_deg / 0.25))

        # Load Data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}
            },
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
        speed_kph = np.sqrt(u_flat**2 + v_flat**2) * 3.6

        # Setup Figure to match target canvas exactly
        plot = Plot(self.map_data.region)
        plot.get_figure()

        speed_bins = [
            (0, 20, base_len),
            (20, 40, base_len + len_step),
            (40, 60, base_len + len_step * 2),
            (60, 80, base_len + len_step * 3),
            (80, 999, base_len + len_step * 4),
        ]

        for s_min, s_max, current_length in speed_bins:
            mask = (speed_kph >= s_min) & (speed_kph < s_max)
            if not np.any(mask):
                continue

            plot.ax.barbs(
                lons_flat[mask],
                lats_flat[mask],
                u_flat[mask],
                v_flat[mask],
                length=current_length,
                linewidth=0.6,
                color=vector_color,
                transform=ccrs.PlateCarree(),
            )

        plot.save_figure(self.output_path)
        ds.close()
        logger.debug("Finished Wind plot...saving")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_wind_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_update(
            remote_url=url,
            cache_file_path=self.grib_path,
            grib_targets=[":UGRD:10 m above ground:", ":VGRD:10 m above ground:"],
        ):
            logger.info("Generating Wind Vectors plot...")
            self.plot()
