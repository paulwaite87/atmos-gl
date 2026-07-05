#!/usr/bin/env python3
import os
import logging
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class TemperatureUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Temperature", map_data)
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None
        self.VMIN_TEMP = -40.0
        self.VMAX_TEMP = 50.0
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "temperature"

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

        # Regional clipping + LOD interpolation
        new_lats, new_lons, temp_smooth = self.regrid_for_lod(
            temp, lats, lons, self.map_region_bbox
        )

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("RdYlBu_r")
        norm = mcolors.Normalize(vmin=self.VMIN_TEMP, vmax=self.VMAX_TEMP)

        plot.ax.contourf(
            new_lons,
            new_lats,
            temp_smooth,
            levels=20,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="both",
            zorder=2,
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        # Key (colourbar) is hour-independent — write it once at the BASE name
        # (temperature_key.png) that the frontend requests, not per-hour.
        self.save_key_image(
            self.output_path,
            cmap,
            norm,
            [-40, -20, 0, 10, 20, 30, 40, 50],
            "Temperature (°C)",
            key_fontsize=self.settings.get("key_fontsize", 8),
        )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(
            [field0["values"]], f"{base}_data.png", self.VMIN_TEMP, self.VMAX_TEMP
        )
        logger.info(f"Finished Temperature texture f{int(self.forecast_hour_str):03d}.")

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "temperature",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )
