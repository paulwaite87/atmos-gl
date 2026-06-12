#!/usr/bin/env python3
import os
import logging
import numpy as np

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_data_texture

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class WindUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Wind", map_data)
        self.VMAX_WIND = 40.0

    def plot(self):
        """Render the static wind barbs PNG (frame 0) + global velocity texture.
        
        Now consumes pre-processed u/v fields from the DB.
        """
        logger.debug(f"Plotting wind to {self.output_path}")

        # Fetch frame 0 from DB
        field0 = self.get_db_field("wind")
        if not field0 or field0["u"] is None or field0["v"] is None:
            logger.warning(
                "Skipping Wind: current-hour u/v fields not available in DB yet."
            )
            return

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

        # --- WebGL multi-frame velocity texture ---
        step = int(self.animation.get("step_hours", 6))
        n_frames = max(2, int(self.animation.get("frames", 2)))
        f_hour_0 = int(self.forecast_hour_str)
        frame_hours = [f_hour_0 + k * step for k in range(n_frames)]

        frames_u = [u]
        frames_v = [v]
        last_good_u, last_good_v = u, v
        live = 1
        for fh in frame_hours[1:]:
            fu, fv = last_good_u, last_good_v
            try:
                field_fh = self.get_db_field_at_hour("wind", fh)
                if field_fh and field_fh["u"] is not None and field_fh["v"] is not None:
                    fu = field_fh["u"]
                    fv = field_fh["v"]
                    last_good_u, last_good_v = fu, fv
                    live += 1
            except Exception as e:
                logger.debug(f"Wind frame f{fh:03d} skipped: {e}")
            frames_u.append(fu)
            frames_v.append(fv)

        base, _ = os.path.splitext(self.output_path)
        encode_data_texture(frames_u[0], frames_u[1] if len(frames_u) > 1 else frames_u[0],
                            f"{base}_data.png", -self.VMAX_WIND, self.VMAX_WIND)
        held = len(frames_u) - live
        logger.info(
            f"Finished Wind plot; "
            f"data texture: {len(frames_u)} frames ({live} live, {held} held)."
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
        field = self.get_db_field("wind")
        if field and field["u"] is not None and field["v"] is not None:
            logger.info("Generating Wind plot and multi-frame velocity texture...")
            self.plot()
        else:
            logger.info(
                "Wind: frame 0 not ready in DB yet (collector may not have run)."
            )
