#!/usr/bin/env python3
import os
import logging
import numpy as np
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from scipy.interpolate import RegularGridInterpolator

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class StormwatchUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Stormwatch", map_data)
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        self.lod_desc = None
        self.VMIN_CAPE = 0.0
        self.VMAX_CAPE = 5000.0

    def save_stormwatch_key(self, output_path):
        """Generates a stormwatch (CAPE) key image."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        fig, ax = plt.subplots(figsize=(4, 0.3))
        key_ticks = [0, 1000, 2000, 3000, 4000, 5000]

        cmap = mpl.cm.get_cmap("YlOrRd")
        norm = mpl.colors.Normalize(vmin=self.VMIN_CAPE, vmax=self.VMAX_CAPE)

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=key_ticks,
        )

        cbar.ax.set_title(
            "CAPE (J/kg)",
            color="white",
            fontsize=self.settings.get("key_fontsize", 8),
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=6)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved stormwatch key to: {key_path}")

    def plot(self, field0):
        """Render the static stormwatch PNG (frame 0, CAPE) + global N-frame texture."""
        
        logger.debug(
            f"Plotting stormwatch for {self.map_data.region.region_identifier}"
        )

        lats = field0["lat"]
        lons = field0["lon"]
        cape = field0["values"]
        # CIN is in values2 but we'll focus on CAPE for the regional render

        # Regional clipping
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0
        lon_idx = (lons >= lon_min - buf) & (lons <= lon_max + buf)
        lat_idx = (lats >= lat_min - buf) & (lats <= lat_max + buf)
        cape_clip = cape[np.ix_(lat_idx, lon_idx)]
        lons_clip = lons[lon_idx]
        lats_clip = lats[lat_idx]

        # LOD interpolation
        if self.level_of_detail == 3:
            step = 0.05
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.125
            self.lod_desc = "medium"
        else:
            step = 0.25
            self.lod_desc = "low"

        new_lats = np.arange(lats_clip.min(), lats_clip.max() + step, step)
        new_lons = np.arange(lons_clip.min(), lons_clip.max() + step, step)

        if lats_clip[0] > lats_clip[-1]:
            lats_inc, cape_inc = lats_clip[::-1], cape_clip[::-1, :]
        else:
            lats_inc, cape_inc = lats_clip, cape_clip

        fn = RegularGridInterpolator(
            (lats_inc, lons_clip), cape_inc, bounds_error=False, fill_value=np.nan
        )
        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        cape_smooth = fn((mesh_lats, mesh_lons))

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("YlOrRd")
        norm = mcolors.Normalize(vmin=self.VMIN_CAPE, vmax=self.VMAX_CAPE)

        plot.ax.contourf(
            new_lons, new_lats, cape_smooth,
            levels=20, cmap=cmap, norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max", zorder=2
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        self.save_stormwatch_key(output_path_for_hour)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL multi-frame data texture (CAPE only) ---
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
                field_fh = self.get_db_field_at_hour("stormwatch", fh)
                if field_fh and field_fh["values"] is not None:
                    pk = field_fh["values"]
                    last_good = pk
                    live += 1
            except Exception as e:
                logger.debug(f"Stormwatch frame f{fh:03d} skipped: {e}")
            frames.append(pk)

        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(frames, f"{base}_data.png", self.VMIN_CAPE, self.VMAX_CAPE)
        held = len(frames) - live
        logger.info(
            f"Finished Stormwatch plot ({self.lod_desc} resolution); "
            f"data texture: {len(frames)} frames ({live} live, {held} held)."
        )

    def run(self):
        self.get_gfs_state()

        field = self.get_db_field("stormwatch")
        if field and field["values"] is not None and self.should_plot_for_hour("stormwatch"):
            logger.info("Generating Stormwatch plot and multi-frame data texture...")
            self.plot(field)
        else:
            if not field or field["values"] is None:
                logger.info(
                    "Stormwatch: frame 0 not ready in DB yet (collector may not have run)."
                )
            else:
                logger.debug("Stormwatch: cached output is fresh, skipping plot.")
