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


class TemperatureUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Temperature", map_data)
        self.level_of_detail = self.settings.get("level_of_detail", 1)
        self.lod_desc = None
        self.VMIN_TEMP = -40.0
        self.VMAX_TEMP = 50.0
        self.per_hour_outputs = [".png", "_data.png"]

    def save_temperature_key(self, output_path):
        """Generates a temperature key image."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"
        # Hour-independent, but regenerated each render cycle so palette / range /
        # font config changes are reflected without manual file deletion.

        fig, ax = plt.subplots(figsize=(4, 0.3))
        key_ticks = [-40, -20, 0, 10, 20, 30, 40, 50]

        cmap = mpl.cm.get_cmap("RdYlBu_r")
        norm = mpl.colors.Normalize(vmin=self.VMIN_TEMP, vmax=self.VMAX_TEMP)

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=key_ticks,
        )

        cbar.ax.set_title(
            "Temperature (°C)",
            color="white",
            fontsize=self.settings.get("key_fontsize", 8),
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=6)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved temperature key to: {key_path}")

    def plot(self, field0):
        """Render the static temperature PNG (this hour) + global data texture.

        Consumes the per-hour field passed by render_all_hours (which fetches the
        correct hour and skips fresh ones), matching the precipitation pattern.
        """
        if not field0 or field0.get("values") is None:
            logger.warning(
                "Skipping Temperature: current-hour field not available in DB yet."
            )
            return

        logger.debug(
            f"Plotting temperature for {self.map_data.region.region_identifier}"
        )

        lats = field0["lat"]
        lons = field0["lon"]
        temp = field0["values"]  # already in Celsius from unpacker

        # Regional clipping
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0
        lon_idx = (lons >= lon_min - buf) & (lons <= lon_max + buf)
        lat_idx = (lats >= lat_min - buf) & (lats <= lat_max + buf)
        temp_clip = temp[np.ix_(lat_idx, lon_idx)]
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
            lats_inc, temp_inc = lats_clip[::-1], temp_clip[::-1, :]
        else:
            lats_inc, temp_inc = lats_clip, temp_clip

        fn = RegularGridInterpolator(
            (lats_inc, lons_clip), temp_inc, bounds_error=False, fill_value=np.nan
        )
        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        temp_smooth = fn((mesh_lats, mesh_lons))

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("RdYlBu_r")
        norm = mcolors.Normalize(vmin=self.VMIN_TEMP, vmax=self.VMAX_TEMP)

        plot.ax.contourf(
            new_lons, new_lats, temp_smooth,
            levels=20, cmap=cmap, norm=norm,
            transform=ccrs.PlateCarree(),
            extend="both", zorder=2
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        # Key (colourbar) is hour-independent — write it once at the BASE name
        # (temperature_key.png) that the frontend requests, not per-hour.
        self.save_temperature_key(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames([field0["values"]], f"{base}_data.png", self.VMIN_TEMP, self.VMAX_TEMP)
        logger.info(f"Finished Temperature texture "
                    f"f{int(self.forecast_hour_str):03d}.")


    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "temperature",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )