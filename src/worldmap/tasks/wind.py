#!/usr/bin/env python3
import os
import math
import logging

import numpy as np
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
from scipy.interpolate import RegularGridInterpolator

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_uv

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# windy.com-style wind-speed ramp (calm -> storm). Kept in sync with the frontend
# PALETTE in ui/modules/wind.js so the matplotlib static heatmap and the GPU per-hour
# heatmap (which shades the velocity texture) look identical.
WIND_PALETTE = [
    (0.25, 0.30, 0.60),   # calm   - deep blue
    (0.15, 0.60, 0.85),   # light  - cyan-blue
    (0.20, 0.75, 0.45),   # breeze - green
    (0.95, 0.90, 0.30),   # fresh  - yellow
    (0.95, 0.55, 0.20),   # strong - orange
    (0.90, 0.20, 0.20),   # gale   - red
    (0.75, 0.25, 0.85),   # storm  - violet
]
WIND_CMAP = mcolors.LinearSegmentedColormap.from_list("windy_wind", WIND_PALETTE)


class WindUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Wind", map_data)
        self.VMAX_WIND = 40.0          # m/s encoding range for the velocity texture
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        # Heatmap speed scale — computed from actual data in run() (global max across all
        # hours, rounded up to the nearest 10 km/h). Initialised to a safe default so
        # plot() can be called standalone without a prior run().
        self.VMAX_SPEED = 100.0 / 3.6   # m/s; overwritten by run()
        # Per hour: windspeed heatmap (.png, like temperature) + velocity texture
        # (_data.png, decoded as u,v by the particle shader AND shaded as speed by the
        # frontend GPU heatmap).
        self.per_hour_outputs = [".png", "_data.png"]

    def plot(self, field0):
        """Render the per-hour windspeed heatmap (.png) + velocity texture (_data.png).

        The particle shader decodes _data.png's rg as (u, v) via `rg * (2*vmax) - vmax`;
        the frontend heatmap re-uses the same texture, computing speed = |(u, v)|. The
        .png is a matplotlib heatmap for the non-stepping (static) view.
        """
        u = field0["u"]  # m/s
        v = field0["v"]  # m/s
        lats = field0["lat"]
        lons = field0["lon"]
        speed = np.hypot(u, v)

        # --- regional windspeed heatmap (mirrors TemperatureUpdater.plot) ---
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0
        lon_idx = (lons >= lon_min - buf) & (lons <= lon_max + buf)
        lat_idx = (lats >= lat_min - buf) & (lats <= lat_max + buf)
        spd_clip = speed[np.ix_(lat_idx, lon_idx)]
        lons_clip = lons[lon_idx]
        lats_clip = lats[lat_idx]

        if self.level_of_detail == 3:
            step = 0.05
        elif self.level_of_detail == 2:
            step = 0.125
        else:
            step = 0.25
        new_lats = np.arange(lats_clip.min(), lats_clip.max() + step, step)
        new_lons = np.arange(lons_clip.min(), lons_clip.max() + step, step)

        if lats_clip[0] > lats_clip[-1]:
            lats_inc, spd_inc = lats_clip[::-1], spd_clip[::-1, :]
        else:
            lats_inc, spd_inc = lats_clip, spd_clip
        fn = RegularGridInterpolator(
            (lats_inc, lons_clip), spd_inc, bounds_error=False, fill_value=np.nan
        )
        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        spd_smooth = fn((mesh_lats, mesh_lons))

        plot = Plot(self.map_data.region)
        plot.get_figure()
        norm = mcolors.Normalize(vmin=0.0, vmax=self.VMAX_SPEED)
        plot.ax.contourf(
            new_lons,
            new_lats,
            spd_smooth,
            levels=20,
            cmap=WIND_CMAP,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max",
            zorder=2,
        )

        out_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(out_for_hour)
        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- velocity texture: raw field; frontend applies direction-coherence live ---
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", self.VMAX_WIND, lat=field0.get("lat"))
        logger.info(
            f"Finished Wind f{int(self.forecast_hour_str):03d} (heatmap .png + R=U,G=V texture)."
        )

    def run(self):
        self.get_gfs_state()

        # --- pre-scan: find global max wind speed across all available hours ----------
        # We want a SINGLE vmax for the whole run so the palette means the same speed
        # at every hour (temporal blending between hours with different scales is wrong).
        # Round up to the nearest 10 km/h so the legend has clean tick values.
        try:
            from worldmap.lib.db import Database
            db = Database()
            hours = db.get_product_hours(self.run_date_str, self.run_id, "wind")
        except Exception as e:
            logger.warning(f"Wind: could not list hours for pre-scan: {e}")
            hours = []

        max_speed_ms = 0.0
        for fh in (hours or []):
            field = self.get_db_field_at_hour("wind", fh)
            if field and field.get("u") is not None and field.get("v") is not None:
                peak = float(np.hypot(field["u"], field["v"]).max())
                if peak > max_speed_ms:
                    max_speed_ms = peak

        if max_speed_ms > 0:
            max_kph = max_speed_ms * 3.6
            rounded_kph = math.ceil(max_kph / 10) * 10   # e.g. 51.7 -> 60
            self.VMAX_SPEED = rounded_kph / 3.6
            logger.info(
                f"Wind: heatmap scale = {rounded_kph} km/h "
                f"(data peak {max_kph:.1f} km/h across {len(hours)} hours)"
            )
        else:
            self.VMAX_SPEED = 100.0 / 3.6
            logger.info("Wind: no field data for pre-scan; using 100 km/h default scale")

        # --- write meta for the frontend (fetched by wind.js to set the shader scale) --
        if self.output_path:
            import json
            meta_path = os.path.join(
                os.path.dirname(self.output_path), "wind_meta.json"
            )
            try:
                with open(meta_path, "w") as f:
                    json.dump({"heatmap_max_kph": round(self.VMAX_SPEED * 3.6)}, f)
            except Exception as e:
                logger.warning(f"Wind: could not write wind_meta.json: {e}")

        # --- render all hours (plot() now has VMAX_SPEED set globally) ----------------
        self.render_all_hours(
            "wind",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
        )