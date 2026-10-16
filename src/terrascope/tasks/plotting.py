#!/usr/bin/env python3
"""Matplotlib figure lifecycle, split out of tasks/common.py (architecture review
candidate "tasks/common.py bundles six unrelated concerns"): the render-task-specific
plotting machinery, as opposed to lib/coastline.py's plain geometry function.
"""
import os
import logging
from typing import TYPE_CHECKING, cast

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes

if TYPE_CHECKING:
    from .common import MapRegion

logger = logging.getLogger(__name__)

WEB_MERCATOR = ccrs.Mercator.GOOGLE  # EPSG:3857
MERCATOR_LAT_LIMIT = 85.0511  # NOTE: just *inside* GOOGLE's 85.0511288 max


def clamp_lats_to_mercator_limit(lats):
    """Clamps a lat array/scalar to just inside Mercator's +-85.0511 deg limit
    (MERCATOR_LAT_LIMIT above). Global GFS/RTOFS grids include the exact +-90 pole
    rows by design (see e.g. lib/unpack.py's CURRENTS_LAT_MIN/MAX comment) -- at
    exactly +-90, Mercator's y = R*ln(tan(pi/4 + lat/2)) is a true mathematical
    singularity (y -> infinity), which PROJ logs as "Invalid latitude" once per point,
    for every contourf/pcolormesh call on every render. Plot.get_figure() already
    clips the visible AXES EXTENT to this same limit, so any row beyond it is already
    invisible off-plot regardless of its exact value -- clamping the DATA array here
    (not masking/dropping rows, which would break contourf/pcolormesh's requirement
    that lats/lons/values stay same-shaped) removes the singularity with no visible
    effect on the render."""
    import numpy as np

    return np.clip(lats, -MERCATOR_LAT_LIMIT, MERCATOR_LAT_LIMIT)


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
