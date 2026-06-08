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
from .common import Updater, MapData, Plot, encode_frames

# Silence warnings from GRIB backend
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)
logging.getLogger("gribapi.bindings").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


# --- WEBGL DATA ENCODER (multi-frame) ---


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)
        # Physical bounds for the shader encoding (must match the frontend).
        # 950hPa (severe cyclone) to 1050hPa (strong anticyclone).
        self.VMIN_PRESSURE = 950.0
        self.VMAX_PRESSURE = 1050.0

    def _load_prmsl(self, path, lon_idx):
        """Open a PRMSL GRIB, standardise longitudes, smooth. Returns (p, lons, lats, lon_idx)."""
        ds = xr.open_dataset(
            path, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}},
        )
        p = ds["prmsl"].values / 100.0
        lons = ds["longitude"].values
        lats = ds["latitude"].values
        if lon_idx is None:
            lon_idx = np.argsort(((lons + 180) % 360) - 180)
        p = p[:, lon_idx]
        lons_sorted = (((lons + 180) % 360) - 180)[lon_idx]
        p_smooth = ndimage.gaussian_filter(p, sigma=1.2)
        ds.close()
        return p_smooth, lons_sorted, lats, lon_idx

    def plot(self):
        """Render the static isobar PNG (from frame 0) AND the N-frame data texture."""
        logger.debug(f"Plotting isobars to {self.output_path}")

        if not os.path.exists(self.frame_paths[0]):
            logger.warning("Skipping Isobars: current-hour frame not available yet.")
            return

        # Frame 0 (current) drives the static contour render and is the first frame.
        p0, lons, lats, lon_idx = self._load_prmsl(self.frame_paths[0], None)

        plot = Plot(self.map_data.region)
        plot.get_figure()

        step = self.settings.get("isobar_step", 4)
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", "white")
        f_size = self.settings.get("label_fontsize", 10)
        thickness = self.settings.get("linewidth", 1.0)
        alpha_val = self.settings.get("alpha", 1.0)

        line_effect = [
            patheffects.withStroke(linewidth=thickness + 1.0, foreground="black", alpha=alpha_val * 0.4)
        ]
        cs = plot.ax.contour(
            lons, lats, p0, levels=levels, colors=color,
            linewidths=thickness, alpha=alpha_val, transform=ccrs.PlateCarree(),
        )
        for collection in getattr(cs, "collections", []):
            collection.set_path_effects(line_effect)
        labels = plt.clabel(cs, fmt="%d", fontsize=f_size, inline=True, colors=color)
        if labels:
            for txt in labels:
                txt.set_alpha(alpha_val)
                txt.set_path_effects(line_effect)
        plot.save_figure(self.output_path)

        # --- WebGL multi-frame data texture ---
        # Resilient: NOMADS often lags the .idx sidecar for the freshest forecast
        # hours, so any frame that isn't ready yet holds the last good frame. This
        # always emits exactly N frames (keeping the frontend's array slicing valid)
        # as long as the current-hour frame loaded.
        frames = [p0]
        last_good = p0
        live = 1
        for path in self.frame_paths[1:]:
            pk = last_good
            if os.path.exists(path):
                try:
                    pk, _, _, _ = self._load_prmsl(path, lon_idx)   # reuse frame-0 lon ordering
                    last_good = pk
                    live += 1
                except Exception as e:
                    logger.warning(f"Isobars frame unreadable ({path}: {e}); holding previous.")
            frames.append(pk)

        base, _ = os.path.splitext(self.output_path)
        encode_frames(frames, f"{base}_data.png", self.VMIN_PRESSURE, self.VMAX_PRESSURE)
        held = len(frames) - live
        logger.info(f"Isobars data texture: {len(frames)} frames ({live} live, {held} held).")

    def run(self):
        self.exit_if_disabled()
        self.get_gfs_state()

        # Animation span: n_frames forecast steps spaced step hours apart, from "now".
        step = int(self.settings.get("animation_step_hours", 6))
        n_frames = max(2, int(self.settings.get("animation_frames", 2)))
        f_hour_0 = int(self.forecast_hour_str)

        self.frame_hours = [f_hour_0 + k * step for k in range(n_frames)]
        self.frame_paths = [
            os.path.join(self.workdir, f"data/gfs_isobars_{h:03d}.grib2")
            for h in self.frame_hours
        ]
        urls = [
            f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/"
            f"gfs.t{self.gfs_run}z.pgrb2.0p25.f{h:03d}"
            for h in self.frame_hours
        ]

        needs_plot = False
        for url, path in zip(urls, self.frame_paths):
            if self.remote_data_update(
                    remote_url=url, cache_file_path=path,
                    grib_targets=[":PRMSL:mean sea level:"]):
                needs_plot = True

        if needs_plot:
            logger.info(f"Generating Isobars plot and {n_frames}-frame data texture...")
            self.plot()