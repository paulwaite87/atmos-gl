#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import scipy.ndimage as ndimage

from matplotlib import patheffects

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

    def plot(self):
        """Renders the isobar transparent PNG with registration fixes."""
        logger.debug(f"Plotting isobars to {self.output_path}")

        # Load GRIB data
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
            },
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

        # New Settings: Read thickness and visibility from config
        thickness = self.settings.getfloat("linewidth", fallback=1.0)
        alpha_val = self.settings.getfloat("alpha", fallback=1.0)

        # High-contrast effects for visibility over dark ocean
        # We scale the stroke thickness based on the configured line thickness
        # and scale the shadow's alpha based on the overall configured alpha
        line_effect = [
            patheffects.withStroke(
                linewidth=thickness + 1.0, foreground="black", alpha=alpha_val * 0.4
            )
        ]

        cs = plot.ax.contour(
            lons,
            lats,
            p_smooth,
            levels=levels,
            colors=color,
            linewidths=thickness,
            alpha=alpha_val,
            transform=ccrs.PlateCarree(),
        )

        # Apply effects to lines
        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(line_effect)

        # Labels
        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels:
            for txt in labels:
                txt.set_alpha(alpha_val)
                txt.set_path_effects(line_effect)

        plot.save_figure(self.output_path)
        ds.close()
        logger.debug("Finished Isobars plot...saving")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()

        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_isobars_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/gfs.t{self.gfs_run}z.pgrb2.0p25.f{self.forecast_hour_str}"
        if self.remote_data_updated(
            remote_url=url,
            cache_file_path=self.grib_path,
            grib_targets=[":PRMSL:mean sea level:"],
        ):
            logger.info("Generating Isobars plot...")
            self.plot()
