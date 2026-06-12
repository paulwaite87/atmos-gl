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

    def plot(self):
        """Render the static isobar PNG (from frame 0) AND the N-frame data texture.
        
        Now consumes pre-processed fields from the DB.
        """
        logger.debug(
            f"Plotting isobars to {self.output_path}"
        )

        # Fetch frame 0 from DB
        field0 = self.get_db_field("isobars")
        if not field0 or field0["values"] is None:
            logger.warning("Skipping Isobars: current-hour field not available in DB yet.")
            return

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
        alpha_val = self.settings.get("alpha", 1.0)

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

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL multi-frame data texture ---
        step = int(self.animation.get("step_hours", 6))
        n_frames = max(2, int(self.animation.get("frames", 2)))
        f_hour_0 = int(self.forecast_hour_str)
        frame_hours = [f_hour_0 + k * step for k in range(n_frames)]

        frames = [field0["values"]]
        last_good = field0["values"]
        live = 1
        for fh in frame_hours[1:]:
            pk = last_good
            try:
                field_fh = self.get_db_field_at_hour("isobars", fh)
                if field_fh and field_fh["values"] is not None:
                    pk = field_fh["values"]
                    last_good = pk
                    live += 1
            except Exception as e:
                logger.debug(f"Isobars frame f{fh:03d} skipped: {e}")
            frames.append(pk)

        base, _ = os.path.splitext(self.output_path)
        encode_frames(
            frames, f"{base}_data.png", self.VMIN_PRESSURE, self.VMAX_PRESSURE
        )
        held = len(frames) - live
        logger.info(
            f"Finished Isobars plot; "
            f"data texture: {len(frames)} frames ({live} live, {held} held)."
        )

    def get_db_field_at_hour(self, product_name: str, fhour: int) -> dict | None:
        """Helper: fetch a field for a specific forecast hour."""
        if not hasattr(self, "gfs_date_str") or not hasattr(self, "gfs_run"):
            return None
        try:
            from worldmap.lib.db import Database
            db = Database()
            return db.get_field(self.gfs_date_str, self.gfs_run, int(fhour), product_name)
        except Exception as e:
            logger.debug(f"get_db_field_at_hour({product_name}, f{fhour:03d}) failed: {e}")
            return None

    def run(self):
        self.exit_if_disabled()
        self.get_gfs_state()

        # Check if frame 0 is available in DB
        field = self.get_db_field("isobars")
        if field and field["values"] is not None:
            logger.info("Generating Isobars plot and multi-frame data texture...")
            self.plot()
        else:
            logger.info(
                "Isobars: frame 0 not ready in DB yet (collector may not have run)."
            )
