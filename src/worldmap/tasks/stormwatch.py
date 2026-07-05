#!/usr/bin/env python3
import os
import logging
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class StormwatchUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Stormwatch", map_data)
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None
        self.VMIN_CAPE = 0.0
        self.VMAX_CAPE = 5000.0
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "stormwatch"

    def save_stormwatch_key(self, output_path):
        """Generates a stormwatch (CAPE) key image."""
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import matplotlib as mpl

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"
        # Hour-independent, but regenerated each render cycle so palette / range /
        # font config changes are reflected without manual file deletion.

        fig = Figure(figsize=(4, 0.3))
        FigureCanvasAgg(fig)
        ax = fig.subplots()
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
        fig.clear()
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

        # Regional clipping + LOD interpolation
        new_lats, new_lons, cape_smooth = self.regrid_for_lod(
            cape, lats, lons, self.map_region_bbox
        )

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("YlOrRd")
        norm = mcolors.Normalize(vmin=self.VMIN_CAPE, vmax=self.VMAX_CAPE)

        plot.ax.contourf(
            new_lons,
            new_lats,
            cape_smooth,
            levels=20,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend="max",
            zorder=2,
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        # Key (colourbar) is hour-independent — write it once at the BASE name
        # (stormwatch_key.png) that the frontend requests, not per-hour.
        self.save_stormwatch_key(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(
            [field0["values"]], f"{base}_data.png", self.VMIN_CAPE, self.VMAX_CAPE
        )
        logger.info(f"Finished Stormwatch texture f{int(self.forecast_hour_str):03d}.")

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "stormwatch",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )
