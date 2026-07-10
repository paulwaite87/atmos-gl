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
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.texture import encode_frames
from .common import Updater, MapData, Plot, MultiHourRenderMixin, ForecastState

# Silence warnings
warnings.filterwarnings("ignore", message=".*missingValue.*")
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class PrecipitationUpdater(Updater, MultiHourRenderMixin):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "Precipitation", map_data)
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None

        # Top of the precip scale (mm/hr). Must match the frontend shader's VMAX.
        # The data texture is sqrt-encoded against this, so most of the 8-bit range
        # is spent on the low rates where precip actually lives (see encode_frames).
        self.VMAX_PRECIP = 100.0
        # Static PNG + GPU data texture.
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "precipitation"

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
        """Standalone key image. Uses a solid-colour ListedColormap over a coarser
        tick set than the map render's alpha-blended contourf cmap — alpha would look
        odd in a legend, and the render's finer BoundaryNorm levels are more detail
        than a legend needs."""
        # Honour the configured palette (matches the map render + the GPU layer),
        # falling back to 'standard' if an unknown palette is set.
        palette_name = self.settings.get("palette", "standard")
        base_colors = self.PALETTES.get(palette_name, self.PALETTES["standard"])
        key_ticks = [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]
        cmap = mcolors.ListedColormap(base_colors)
        norm = mcolors.BoundaryNorm(key_ticks, cmap.N)

        self.save_key_image(
            output_path,
            cmap,
            norm,
            key_ticks,
            "Precipitation (mm/hr)",
            key_fontsize=self.settings.get("key_fontsize", 8),
        )

    def plot(self, field0, state: ForecastState):
        """Static region render (frame 0) + colourbar key + global N-frame texture.

        Now consumes pre-processed fields from the DB instead of opening GRIBs.
        Outputs are cached per-hour: {basename}_f{fhour:03d}.png
        """
        logger.debug(
            f"Plotting precipitation for {self.map_data.region.region_identifier}"
        )

        min_rate = self.settings.get("min_mm_hr", 0.1)
        alpha = float(self.settings.get("opacity", 50) / 100)
        palette_name = self.settings.get("palette", "standard")

        # --- Static region render (frame 0) ---
        lats = field0["lat"]
        lons = field0["lon"]
        prate = field0["values"].copy()
        prate[prate < min_rate] = 0.0

        # Regional clipping + LOD interpolation (fill_value=0: gaps read as "no rain").
        # Level-of-detail also drives the post-interpolation smoothing strength below.
        new_lats, new_lons, prate_smooth = self.regrid_for_lod(
            prate, lats, lons, self.map_region_bbox, fill_value=0
        )
        filter_sigma = {"high": 1.2, "medium": 0.8}.get(self.lod_desc, 0.0)

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
        output_path_for_hour = self.get_output_path_for_hour(state.fhour)
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
        smoothed = self._smooth_global_field(
            field0["lat"], field0["lon"], field0["values"]
        )
        encode_frames(
            [smoothed], f"{base}_data.png", 0.0, self.VMAX_PRECIP, transform="sqrt"
        )
        logger.info(
            f"Finished Precipitation texture "
            f"f{state.fhour:03d} ({self.lod_desc} smoothing)."
        )

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
            new_lats = np.linspace(
                lat_inc[0], lat_inc[-1], (len(lat_inc) - 1) * factor + 1
            )
            new_lons = np.linspace(lons[0], lons[-1], (len(lons) - 1) * factor + 1)
            mlat, mlon = np.meshgrid(new_lats, new_lons, indexing="ij")
            arr = fn((mlat, mlon)).astype(np.float32)
            if len(lats) > 1 and lats[0] > lats[-1]:
                arr = arr[::-1, :]  # restore north-first row order for the texture

        sigma = base_sigma * factor
        if sigma > 0:
            arr = gaussian_filter(arr, sigma=sigma)
        return arr

    def run(self, max_hours=None):
        # Warms the shared per-cycle GFS baseline cache (map_data.shared_state) for
        # other updaters this cycle; render_all_hours resolves its own state from the
        # catalog below, so the return value here is unused.
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            "precipitation",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
            max_hours=max_hours,
        )