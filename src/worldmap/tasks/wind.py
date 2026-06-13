#!/usr/bin/env python3
import os
import logging
import numpy as np

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_uv

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class WindUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Wind", map_data)
        self.VMAX_WIND = 40.0

    def plot(self, field0):
        """Render the static wind barbs PNG (frame 0) + global velocity texture."""
        logger.debug(f"Plotting wind to {self.output_path}")

        lats = field0["lat"]
        lons = field0["lon"]
        u = field0["u"]  # m/s
        v = field0["v"]  # m/s

        # Regional barbs render
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Subsample for visual density
        subsample = self.settings.get("barb_density", 8)
        lats_sub = lats[::subsample]
        lons_sub = lons[::subsample]
        u_sub = u[::subsample, ::subsample]
        v_sub = v[::subsample, ::subsample]

        plot.ax.barbs(
            lons_sub, lats_sub, u_sub, v_sub,
            transform=__import__("cartopy.crs", fromlist=["PlateCarree"]).PlateCarree(),
            length=5, linewidth=0.5, alpha=0.8, zorder=2
        )

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL global velocity texture (R=U east, G=V north) ---
        # The particle shader (ui/modules/_windparticles.js) samples ONE velocity
        # field and advects particles along it; motion comes from the particles, not
        # from interpolating the texture. So we encode frame 0's u/v with encode_uv
        # (NOT encode_data_texture, which packs two timesteps of a single scalar and
        # leaves the shader reading u as both components -> particles fly off-pattern).
        base, _ = os.path.splitext(self.output_path)
        encode_uv(u, v, f"{base}_data.png", self.VMAX_WIND)
        logger.info("Finished Wind plot; velocity texture written (R=U, G=V).")

    def run(self):
        self.get_gfs_state()

        # Check if frame 0 is available in DB AND is newer than cached output
        field = self.get_db_field("wind")
        if field and field["u"] is not None and field["v"] is not None and self.should_plot_for_hour("wind"):
            logger.info("Generating Wind plot and multi-frame velocity texture...")
            self.plot(field)
        else:
            if not field or field["u"] is None or field["v"] is None:
                logger.info(
                    "Wind: frame 0 not ready in DB yet (collector may not have run)."
                )
            else:
                logger.debug("Wind: cached output is fresh, skipping plot.")