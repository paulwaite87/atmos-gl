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
        # Static PNG + GPU data texture.
        self.per_hour_outputs = [".png", "_data.png"]

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
        # Hour-independent, but regenerated each render cycle so palette / range /
        # font config changes are reflected without manual file deletion.

        fig, ax = plt.subplots(figsize=(4, 0.3))
        key_ticks = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]

        # Honour the configured palette (matches the map render + the GPU layer),
        # falling back to 'standard' if an unknown palette is set.
        palette_name = self.settings.get("palette", "standard")
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        cmap = mpl.colors.ListedColormap(base_colors)
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
        # Key (colourbar) is hour-independent — write at the BASE name
        # (precipitation_key.png) that the frontend requests, not per-hour.
        self.save_precipitation_key(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        # Smooth the GLOBAL field before encoding so the banded LUT produces smooth
        # band boundaries instead of tracing the raw 0.25-deg grid (the old static
        # render smoothed its regional clip the same way; the texture never did).
        base, _ = os.path.splitext(output_path_for_hour)
        smoothed = self._smooth_global_field(field0["lat"], field0["lon"], field0["values"])
        encode_frames([smoothed], f"{base}_data.png", 0.0, self.VMAX_PRECIP, transform="sqrt")
        logger.info(f"Finished Precipitation texture "
                    f"f{int(self.forecast_hour_str):03d} ({self.lod_desc} smoothing).")

    def _smooth_global_field(self, lats, lons, values):
        """Upsample + Gaussian-blur the global precip field for a smooth texture.

        Tied to level_of_detail (reusing that setting's 3 levels), bounded for a
        GLOBAL grid so the texture stays a sane size:
            LOD 1 (low):    1x native 0.25 deg, light blur   (~4 MB/hr)
            LOD 2 (medium): 2x -> 0.125 deg,    medium blur   (~17 MB/hr)
            LOD 3 (high):   3x -> 0.083 deg,     stronger blur (~37 MB/hr)
        Blur sigma scales with the upsample factor so the PHYSICAL smoothing radius
        (~1.2 native cells) stays roughly constant across levels. The GPU also
        bilinear-filters the texture at render time, so even 1x looks smooth.
        """
        lod = int(getattr(self, "level_of_detail", 1) or 1)
        if lod >= 3:
            factor, base_sigma = 3, 1.2
        elif lod == 2:
            factor, base_sigma = 2, 1.2
        else:
            factor, base_sigma = 1, 1.2

        arr = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=0.0)

        if factor > 1:
            # Bilinear upsample onto a regular factor-x denser global grid.
            lat_inc = lats[::-1] if (len(lats) > 1 and lats[0] > lats[-1]) else lats
            src = arr[::-1, :] if (len(lats) > 1 and lats[0] > lats[-1]) else arr
            fn = RegularGridInterpolator(
                (lat_inc, lons), src, bounds_error=False, fill_value=0.0
            )
            new_lats = np.linspace(lat_inc[0], lat_inc[-1], (len(lat_inc) - 1) * factor + 1)
            new_lons = np.linspace(lons[0], lons[-1], (len(lons) - 1) * factor + 1)
            mlat, mlon = np.meshgrid(new_lats, new_lons, indexing="ij")
            arr = fn((mlat, mlon)).astype(np.float32)
            if (len(lats) > 1 and lats[0] > lats[-1]):
                arr = arr[::-1, :]  # restore north-first row order for the texture

        sigma = base_sigma * factor
        if sigma > 0:
            arr = gaussian_filter(arr, sigma=sigma)
        return arr

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "precipitation",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )