#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.ndimage import gaussian_filter
from scipy.interpolate import RegularGridInterpolator

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

    def plot(self, field0):
        """Static region render (frame 0) + colourbar key + global N-frame texture.
        
        Now consumes pre-processed fields from the DB instead of opening GRIBs.
        Outputs are cached per-hour: {basename}_f{fhour:03d}.png
        """
        logger.debug(
            f"Plotting precipitation for {self.map_data.region.region_identifier}"
        )

        min_rate = self.settings.get("min_mm_hr", 0.1)
        alpha = float(self.settings.get("alpha", 50) / 100)
        palette_name = self.settings.get("palette", "standard")

        # --- Static region render (frame 0) ---
        lats = field0["lat"]
        lons = field0["lon"]
        prate = field0["values"]

        # Define BBox with a small buffer for smooth edges
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        # Clip to region
        lon_idx = (lons >= lon_min - buf) & (lons <= lon_max + buf)
        lat_idx = (lats >= lat_min - buf) & (lats <= lat_max + buf)
        prate_clip = prate[np.ix_(lat_idx, lon_idx)]
        lons_clip = lons[lon_idx]
        lats_clip = lats[lat_idx]

        prate_clip = prate_clip.copy()
        prate_clip[prate_clip < min_rate] = 0.0

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

        new_lats = np.arange(lats_clip.min(), lats_clip.max() + step, step)
        new_lons = np.arange(lons_clip.min(), lons_clip.max() + step, step)

        # Handle latitude ordering for Interpolator (must be strictly increasing)
        if lats_clip[0] > lats_clip[-1]:
            lats_inc, prate_inc = lats_clip[::-1], prate_clip[::-1, :]
        else:
            lats_inc, prate_inc = lats_clip, prate_clip

        fn = RegularGridInterpolator(
            (lats_inc, lons_clip), prate_inc, bounds_error=False, fill_value=0
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

        # Per-hour output path: precipitation_f003.png (for f003 forecast hour)
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        self.save_precipitation_key(output_path_for_hour)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL multi-frame data texture (global, sqrt-encoded) ---
        # Fetch all animation frames from the DB. If a frame is missing, hold the last
        # good one (same resilience as before, but now from DB not file system).
        step = int(self.animation.get("step_hours", 6))
        n_frames = max(2, int(self.animation.get("frames", 2)))
        f_hour_0 = int(self.forecast_hour_str)
        frame_hours = [f_hour_0 + k * step for k in range(n_frames)]

        frames = [field0["values"]]  # frame 0 is already loaded
        last_good = field0["values"]
        live = 1
        for fh in frame_hours[1:]:
            pk = last_good
            try:
                # Compute the actual forecast hour string for this frame
                field_fh = self.get_db_field_at_hour("precipitation", fh)
                if field_fh and field_fh["values"] is not None:
                    pk = field_fh["values"]
                    last_good = pk
                    live += 1
            except Exception as e:
                logger.debug(f"Precipitation frame f{fh:03d} skipped: {e}")
            frames.append(pk)

        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(
            frames, f"{base}_data.png", 0.0, self.VMAX_PRECIP, transform="sqrt"
        )
        held = len(frames) - live
        logger.info(
            f"Finished Precipitation plot ({self.lod_desc} resolution); "
            f"data texture: {len(frames)} frames ({live} live, {held} held)."
        )

    def run(self):
        self.get_gfs_state()

        # Check if frame 0 (current hour) is available in the DB AND is newer than cached output
        field = self.get_db_field("precipitation")
        if field and field["values"] is not None and self.should_plot_for_hour("precipitation"):
            logger.info("Generating Precipitation plot and multi-frame data texture...")
            self.plot(field)
        else:
            if not field or field["values"] is None:
                logger.info(
                    "Precipitation: frame 0 not ready in DB yet (collector may not have run)."
                )
            else:
                logger.debug("Precipitation: cached output is fresh, skipping plot.")
