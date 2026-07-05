#!/usr/bin/env python3
"""One renderer for every single-scalar contourf heatmap, driven by a small spec.

temperature, ozone and stormwatch (CAPE) render identically — regional LOD regrid,
a 20-level `contourf`, a per-hour PNG, a hour-independent colourbar key, and a WebGL
data texture — differing only in colormap, value range, `extend` mode, key ticks and
title. That variation is captured in `ScalarFieldSpec`; `SPECS` holds the three
instances and `ScalarFieldUpdater` consumes one. Adding a fourth scalar field is a
single `SPECS` entry plus one `TASK_CLASSES` line in layer_builder.

Validated with ast.parse.
"""
import os
import logging
from dataclasses import dataclass

import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot, encode_frames

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScalarFieldSpec:
    """Everything that varies between the scalar-field heatmaps.

    `product` is the catalog/status key, passed to render_all_hours, reported as
    status_product, and used as the Updater section name for settings lookup (Updater
    lowercases the section, so one identifier serves all three roles).
    """

    product: str
    cmap: str
    vmin: float
    vmax: float
    extend: str
    ticks: list
    title: str


SPECS = {
    "temperature": ScalarFieldSpec(
        product="temperature",
        cmap="RdYlBu_r",
        vmin=-40.0,
        vmax=50.0,
        extend="both",
        ticks=[-40, -20, 0, 10, 20, 30, 40, 50],
        title="Temperature (°C)",
    ),
    "ozone": ScalarFieldSpec(
        product="ozone",
        cmap="viridis",
        vmin=200.0,
        vmax=450.0,
        extend="both",
        ticks=[200, 250, 300, 350, 400, 450],
        title="Ozone (DU)",
    ),
    "stormwatch": ScalarFieldSpec(
        product="stormwatch",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=5000.0,
        extend="max",
        ticks=[0, 1000, 2000, 3000, 4000, 5000],
        title="CAPE (J/kg)",
    ),
}


class ScalarFieldUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData, spec: ScalarFieldSpec):
        super().__init__(config, spec.product, map_data)
        self.spec = spec
        self.level_of_detail = int(self.settings.get("level_of_detail", 1))
        self.lod_desc = None
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = spec.product

    def plot(self, field0):
        """Render the static PNG (this hour) + global data texture for one scalar field.

        Consumes the per-hour field passed by render_all_hours (which fetches the
        correct hour and skips fresh ones), matching the precipitation pattern.
        """
        if not field0 or field0.get("values") is None:
            logger.warning(
                f"Skipping {self.section}: current-hour field not available in DB yet."
            )
            return

        logger.debug(
            f"Plotting {self.status_product} for {self.map_data.region.region_identifier}"
        )

        lats = field0["lat"]
        lons = field0["lon"]
        values = field0["values"]

        # Regional clipping + LOD interpolation
        new_lats, new_lons, values_smooth = self.regrid_for_lod(
            values, lats, lons, self.map_region_bbox
        )

        plot = Plot(self.map_data.region)
        plot.get_figure()

        cmap = __import__("matplotlib.cm", fromlist=["get_cmap"]).get_cmap(self.spec.cmap)
        norm = mcolors.Normalize(vmin=self.spec.vmin, vmax=self.spec.vmax)

        plot.ax.contourf(
            new_lons,
            new_lats,
            values_smooth,
            levels=20,
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            extend=self.spec.extend,
            zorder=2,
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        plot.save_figure(output_path_for_hour)
        # Key (colourbar) is hour-independent — write it once at the BASE name
        # (<product>_key.png) that the frontend requests, not per-hour.
        self.save_key_image(
            self.output_path,
            cmap,
            norm,
            self.spec.ticks,
            self.spec.title,
            key_fontsize=self.settings.get("key_fontsize", 8),
        )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour;
        # the frontend scrubber assembles the animation from consecutive hours) ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames(
            [field0["values"]], f"{base}_data.png", self.spec.vmin, self.spec.vmax
        )
        logger.info(f"Finished {self.section} texture f{int(self.forecast_hour_str):03d}.")

    def run(self):
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has
        # a PNG for each hour. should_plot_for_hour skips hours already fresh.
        self.render_all_hours(
            self.status_product,
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
        )
