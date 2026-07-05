#!/usr/bin/env python3
import os
import logging
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class OzoneUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Ozone", map_data)
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None
        # Ozone units vary; these are typical for TOZNE (Dobson Units)
        self.VMIN_OZONE = 200.0
        self.VMAX_OZONE = 450.0
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "ozone"

    def save_ozone_key(self, output_path):
        """Generates an ozone key image."""
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
        key_ticks = [200, 250, 300, 350, 400, 450]

        cmap = mpl.cm.get_cmap("viridis")
        norm = mpl.colors.Normalize(vmin=self.VMIN_OZONE, vmax=self.VMAX_OZONE)

        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=key_ticks,
        )

        cbar.ax.set_title(
            "Ozone (DU)",
            color="white",
            fontsize=self.settings.get("key_fontsize", 8),
            pad=2,
        )
        cbar.ax.tick_params(colors="white", labelsize=6)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        fig.clear()
        logger.debug(f"Saved ozone key to: {key_path}")

    def plot(self, field0):
        """Render the static ozone PNG (frame 0) + global N-frame texture.

        Now consumes pre-processed fields from the DB.
        """
        logger.debug(f"Plotting ozone for {self.map_data.region.region_identifier}")

        lats = field0["lat"]
        lons = field0["lon"]
        ozone = field0["values"]

        # Regional clipping + LOD interpolation
        new_lats, new_lons, ozone_smooth = self.regrid_for_lod(
            ozone, lats, lons, self.map_region_bbox
        )

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap("viridis")
        norm = mcolors.Normalize(vmin=self.VMIN_OZONE, vmax=self.VMAX_OZONE)

        plot.ax.contourf(
            new_lons,
            new_lats,
            ozone_smooth,
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
        # (ozone_key.png) that the frontend requests, not per-hour.
        self.save_ozone_key(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(
            [field0["values"]], f"{base}_data.png", self.VMIN_OZONE, self.VMAX_OZONE
        )
        logger.info(f"Finished Ozone texture f{int(self.forecast_hour_str):03d}.")

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            "ozone",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )
