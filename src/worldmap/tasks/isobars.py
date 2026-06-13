#!/usr/bin/env python3
import os
import logging
import numpy as np
import matplotlib.patheffects as patheffects
import matplotlib.pyplot as plt
import cartopy.crs as ccrs

from scipy.interpolate import RegularGridInterpolator

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("gribapi.bindings").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class IsobarUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Isobars", map_data)
        # Physical bounds for the shader encoding (must match the frontend).
        # 950hPa (severe cyclone) to 1050hPa (strong anticyclone).
        self.VMIN_PRESSURE = 950.0
        self.VMAX_PRESSURE = 1050.0

    def plot(self, field0):
        """Render the static isobar PNG (from frame 0) AND the N-frame data texture.
        
        Now consumes pre-processed fields from the DB.
        Outputs are cached per-hour.
        """
        logger.debug(
            f"Plotting isobars to per-hour output path"
        )

        lats = field0["lat"]
        lons = field0["lon"]
        p = field0["values"]  # already smoothed from unpacker

        plot = Plot(self.map_data.region)
        plot.get_figure()

        step = self.settings.get("isobar_step", 4)
        levels = np.arange(940, 1060, step)
        color = self.settings.get("isobar_color", "white")
        f_size = self.settings.get("label_fontsize", 10)
        thickness = self.settings.get("linewidth", 1.0)
        alpha_val = float(self.settings.get("alpha", 100) / 100)

        line_effect = [
            patheffects.withStroke(linewidth=thickness + 2, foreground="black")
        ]

        plot.ax.contour(
            lons,
            lats,
            p,
            levels=levels,
            colors=color,
            linewidths=thickness,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )

        # Add labels at isosurfaces
        cs = plot.ax.contour(
            lons,
            lats,
            p,
            levels=levels[::2],  # Label every other level
            colors=color,
            linewidths=thickness,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )

        plot.ax.clabel(
            cs,
            inline=True,
            fontsize=f_size,
            fmt="%1.0f",
            colors=color,
            manual=False,
        )

        for text in plot.ax.texts:
            text.set_path_effects(line_effect)
            text.set_alpha(alpha_val)

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames([field0["values"]], f"{base}_data.png", self.VMIN_PRESSURE, self.VMAX_PRESSURE)
        logger.info(f"Finished Isobars texture "
                    f"f{int(self.forecast_hour_str):03d}.")

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "isobars",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )
