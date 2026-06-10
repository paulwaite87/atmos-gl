#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.ndimage import gaussian_filter

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PrecipitationUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Precipitation", map_data)
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        self.lod_desc = None

        # Top of the precip scale (mm/hr). Must match the frontend shader's VMAX.
        # The data texture is sqrt-encoded against this, so most of the 8-bit range
        # is spent on the low rates where precip actually lives (see encode_frames).
        self.VMAX_PRECIP = 100.0

        self.PALETTES = {
            "standard": [
                (0.0, 1.0, 1.0),
                (0.0, 0.5, 1.0),
                (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.5, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0),
            ],
            "ocean_blue": [
                (0.8, 0.9, 1.0),
                (0.6, 0.8, 1.0),
                (0.4, 0.6, 1.0),
                (0.2, 0.4, 1.0),
                (0.0, 0.2, 0.8),
                (0.0, 0.0, 0.6),
                (0.0, 0.0, 0.4),
            ],
            "high_contrast": [
                (0.0, 0.9, 0.0),
                (0.0, 0.6, 0.0),
                (1.0, 1.0, 0.0),
                (1.0, 0.6, 0.0),
                (1.0, 0.0, 0.0),
                (0.7, 0.0, 0.0),
                (1.0, 0.0, 1.0),
            ],
        }

    def save_precipitation_key(self, output_path):
        """Generates a standalone key image using a standardized naming strategy."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl
        import os

        # Standardize naming: take base name, add _key, append extension
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        fig, ax = plt.subplots(figsize=(4, 0.3))
        key_ticks = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]

        # Use your existing colormap logic
        cmap = mpl.colors.ListedColormap(self.PALETTES["standard"])
        norm = mpl.colors.BoundaryNorm(key_ticks, cmap.N)

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=key_ticks,
        )

        cbar.ax.set_title(
            "Precipitation (mm/hr)",
            color="white",
            fontsize=self.settings.get("key_fontsize", 8),
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=6)

        # 2. Save key separately
        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved precipitation key to: {key_path}")

    def _load_prate_global(self, path):
        """Global instantaneous precip rate in mm/hr.

        Longitudes standardized to -180..180 ascending; latitude left in the GFS
        native (descending) order so row 0 is the northern edge -- the row order
        the WebGL data texture and shader expect (matches isobars)."""
        ds = xr.open_dataset(path, engine="cfgrib")
        ds = ds.assign_coords(longitude=(((ds.longitude + 180) % 360) - 180))
        ds = ds.sortby("longitude")
        prate = ds["prate"].values.squeeze() * 3600.0
        ds.close()
        del ds
        # Smooth the native 0.25 deg field before it becomes a data-texture frame.
        # Raw PRATE is very speckly; as a magnitude-keyed colour fill (unlike isobars'
        # contour lines) that speckle crosses the min_mm_hr threshold differently each
        # interpolated frame and shimmers globally. isobars smooths PRMSL the same way.
        prate = gaussian_filter(prate, sigma=1.0)
        return prate

    def plot(self):
        """Static region render (frame 0) + colourbar key + global N-frame texture."""
        from scipy.interpolate import RegularGridInterpolator
        import gc  # Garbage collector

        if not os.path.exists(self.frame_paths[0]):
            logger.warning(
                "Skipping Precipitation: current-hour frame not available yet."
            )
            return

        logger.debug(
            f"Plotting precipitation for {self.map_data.region.region_identifier}"
        )

        min_rate = self.settings.get("min_mm_hr", 0.1)
        alpha = self.settings.get("alpha", 0.5)
        palette_name = self.settings.get("palette", "standard")

        # Load Dataset and Clip Immediately
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
            longitude=slice(lon_min - buf, lon_max + buf),
        )

        prate = ds_clipped["prate"].values.squeeze() * 3600.0
        # Apply the minimum threshold to clip out trace noise
        prate[prate < min_rate] = 0.0

        lons = ds_clipped.longitude.values
        lats = ds_clipped.latitude.values

        # Explicit cleanup of the large dataset
        ds.close()
        del ds
        gc.collect()

        # 2. DYNAMIC RESAMPLING (Level of Detail Logic)
        if self.level_of_detail == 3:
            step = 0.05  # High resolution (very smooth)
            filter_sigma = 1.2
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.125  # Medium resolution
            filter_sigma = 0.8
            self.lod_desc = "medium"
        else:
            step = 0.25  # Low resolution (Native GFS grid size)
            filter_sigma = 0.0  # No smoothing
            self.lod_desc = "low"

        new_lats = np.arange(lats.min(), lats.max() + step, step)
        new_lons = np.arange(lons.min(), lons.max() + step, step)

        # Handle latitude ordering for Interpolator (must be strictly increasing)
        if lats[0] > lats[-1]:
            lats_inc, prate_inc = lats[::-1], prate[::-1, :]
        else:
            lats_inc, prate_inc = lats, prate

        fn = RegularGridInterpolator(
            (lats_inc, lons), prate_inc, bounds_error=False, fill_value=0
        )

        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        prate_smooth = fn((mesh_lats, mesh_lons))

        # Setup Plotting
        plot = Plot(self.map_data.region)
        plot.get_figure()

        levels = [0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0]
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        rgba_colors = [(*c, alpha) for c in base_colors]

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "smooth_precip", rgba_colors, N=256
        )
        norm = mcolors.BoundaryNorm(levels, cmap.N)

        # Render Heatmap Contour
        prate_smooth = gaussian_filter(prate_smooth, sigma=filter_sigma)
        plot.ax.contourf(
            new_lons,
            new_lats,
            prate_smooth,
            levels=levels,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max",
            antialiased=True,
            zorder=2,
        )

        plot.save_figure(self.output_path)

        self.save_precipitation_key(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL multi-frame data texture (global, sqrt-encoded) ---
        # Global PRATE per forecast frame; the frontend interpolates between frames
        # on the GPU and colourises via the palette LUT. Resilient like isobars:
        # NOMADS often lags the .idx sidecar for the freshest hours, so any frame
        # that isn't ready holds the last good frame -- always emitting exactly N
        # frames (keeping the frontend's array slicing valid).
        try:
            p0 = self._load_prate_global(self.frame_paths[0])
        except Exception as e:
            logger.warning(
                f"Precipitation: could not load current frame for texture ({e})."
            )
            return
        frames = [p0]
        last_good = p0
        live = 1
        for path in self.frame_paths[1:]:
            pk = last_good
            if os.path.exists(path):
                try:
                    pk = self._load_prate_global(path)
                    last_good = pk
                    live += 1
                except Exception as e:
                    logger.warning(
                        f"Precipitation frame unreadable ({path}: {e}); holding previous."
                    )
            frames.append(pk)

        base, _ = os.path.splitext(self.output_path)
        encode_frames(
            frames, f"{base}_data.png", 0.0, self.VMAX_PRECIP, transform="sqrt"
        )
        held = len(frames) - live
        logger.info(
            f"Finished Precipitation plot ({self.lod_desc} resolution); "
            f"data texture: {len(frames)} frames ({live} live, {held} held)."
        )

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()

        # Animation span: n_frames forecast steps spaced step hours apart, from "now".
        step = int(self.animation.get("step_hours", 6))
        n_frames = max(2, int(self.animation.get("frames", 2)))
        f_hour_0 = int(self.forecast_hour_str)

        self.frame_hours = [f_hour_0 + k * step for k in range(n_frames)]
        self.frame_paths = [
            self.cache_path(f"gfs_precip_{h:03d}.grib2") for h in self.frame_hours
        ]
        # Frame 0 (current hour) drives the static region render + colourbar key.
        self.grib_path = self.frame_paths[0]

        urls = [
            f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/atmos/"
            f"gfs.t{self.gfs_run}z.pgrb2.0p25.f{h:03d}"
            for h in self.frame_hours
        ]

        needs_plot = False
        for url, path in zip(urls, self.frame_paths):
            if self.remote_data_update(
                remote_url=url,
                cache_file_path=path,
                grib_targets=[":PRATE:surface:"],
            ):
                needs_plot = True

        if needs_plot:
            logger.info(
                f"Generating Precipitation plot and {n_frames}-frame data texture..."
            )
            self.plot()
