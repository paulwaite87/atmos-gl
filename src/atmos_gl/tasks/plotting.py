#!/usr/bin/env python3
"""Matplotlib figure lifecycle, split out of tasks/common.py (architecture review
candidate "tasks/common.py bundles six unrelated concerns"): the render-task-specific
plotting machinery, as opposed to lib/coastline.py's plain geometry function.

PlottingMixin is mixed into Updater (see common.py) the same way MultiHourRenderMixin
is -- save_key_image only ever used self.section for its debug log, so moving it here
as a mixin method keeps every existing self.save_key_image(...) call site unchanged.
"""
import os
import logging
from typing import TYPE_CHECKING, cast

import matplotlib as mpl
import matplotlib.colors as mcolors
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes

if TYPE_CHECKING:
    from .common import MapRegion

logger = logging.getLogger(__name__)

WEB_MERCATOR = ccrs.Mercator.GOOGLE  # EPSG:3857
MERCATOR_LAT_LIMIT = 85.0511  # NOTE: just *inside* GOOGLE's 85.0511288 max


def opaque_cmap(cmap, n=256):
    """Return an opaque copy of a colormap (alpha forced to 1.0)."""
    import numpy as np

    colors = cmap(np.linspace(0, 1, n))  # (n, 4) RGBA
    colors[:, 3] = 1.0
    return mcolors.ListedColormap(colors)


class Plot:
    def __init__(self, region: "MapRegion", projection=WEB_MERCATOR):
        self.region = region
        self.projection = projection
        self.fig = None
        self.ax = None

    def get_figure(self):
        plot_target_width = float(self.region.target_width) / 100
        plot_target_height = float(self.region.target_height) / 100
        # OO figure with an explicit Agg canvas — no pyplot, no global state, thread-safe.
        self.fig = Figure(figsize=(plot_target_width, plot_target_height), dpi=100)
        FigureCanvasAgg(self.fig)
        self.ax = cast(
            geoaxes.GeoAxes, self.fig.add_axes((0, 0, 1, 1), projection=self.projection)
        )
        bbox = self.region.bbox
        lat_lo = max(bbox[1], -MERCATOR_LAT_LIMIT)
        lat_hi = min(bbox[3], MERCATOR_LAT_LIMIT)
        # extent is ALWAYS given in lon/lat degrees, regardless of axes projection
        self.ax.set_extent([bbox[0], bbox[2], lat_lo, lat_hi], crs=ccrs.PlateCarree())
        self.ax.set_aspect("auto", adjustable="box")

    def save_figure(self, output_path: str):
        self.ax.set_axis_off()
        self.ax.patch.set_alpha(0)
        self.fig.patch.set_alpha(0)

        # Atomic write/move to avoid timing issues
        base, ext = os.path.splitext(output_path)
        tmp_img = f"{base}.tmp{ext}"
        self.fig.savefig(tmp_img, transparent=True, bbox_inches=None, pad_inches=0)
        os.replace(tmp_img, output_path)

        # No global figure registry to close; just release the artists.
        self.fig.clear()


class PlottingMixin:
    """save_key_image, mixed into Updater. Assumes self.section (for its debug log)."""

    def save_key_image(
        self,
        output_path,
        cmap,
        norm,
        ticks,
        title,
        *,
        key_fontsize=8,
        labelsize=6,
        tick_format=None,
        weight=None,
        decorate=None,
    ):
        """Render a standalone horizontal colourbar key PNG at `<base>_key<ext>`.

        Shared scaffold absorbed from the near-identical save_*_key methods every
        legend-bearing layer (ozone/temperature/stormwatch/precipitation/currents/sst/
        waves) built independently. Callers supply their own cmap/norm (often reused
        from their contourf() call) plus the layer-specific ticks and title.
        `decorate`, if given, is called with the built colourbar before its title/
        ticks are styled, for callers that annotate the bar itself (e.g. waves'
        below-threshold shading).
        """
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        fig = Figure(figsize=(4, 0.3))
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=ticks,
        )
        if tick_format is not None:
            from matplotlib.ticker import FormatStrFormatter

            cbar.ax.xaxis.set_major_formatter(FormatStrFormatter(tick_format))
        if decorate is not None:
            decorate(cbar)
        cbar.ax.set_title(title, color="white", fontsize=key_fontsize, pad=2, weight=weight)
        cbar.ax.tick_params(colors="white", labelsize=labelsize)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        fig.clear()
        logger.debug(f"{self.section}: saved key to {key_path}")
