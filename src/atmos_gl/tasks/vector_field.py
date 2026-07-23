#!/usr/bin/env python3
"""Shared base for particle-only vector-field layers -- a per-hour u/v velocity
texture (encode_uv, no static heatmap PNG) with a selectable-palette legend key.
Currently CurrentsUpdater and JetStreamUpdater.

Extracted per CLAUDE.md's "Prefer Common Code" directive: JetStreamUpdater turned out
to need CurrentsUpdater's exact shape (no heatmap, fixed VMAX, own palette + key), not
WindUpdater's (heatmap + particles, per-run dynamic VMAX pre-scan) -- the original
scoping guess before comparing the two directly.

Deepens the template-method pattern already established elsewhere in tasks/ (see
CLAUDE.md's "Deepening Template-Method Hierarchies"): this base owns run()'s control
flow and the palette + key machinery; each subclass sets the VMAX/PALETTES/
DEFAULT_PALETTE/KEY_TITLE/KEY_TICK_FORMAT class attributes and implements plot() (the
per-layer field processing, ending in an encode_uv call) and _warm_baseline_cache()
(which forecast source -- GFS or RTOFS -- this layer's run comes from).
"""
import logging

import numpy as np
import matplotlib.colors as mcolors

from atmos_gl.lib.config import AtmosGLConfig
from .common import Updater, MapData, MultiHourRenderMixin
from .plotting import opaque_cmap

logger = logging.getLogger(__name__)


class VectorFieldUpdater(Updater, MultiHourRenderMixin):
    """Base for a per-hour u/v velocity-texture-only layer with a selectable-palette
    legend key. self.status_product defaults to self.section (the common case for
    every current consumer); override it post-super().__init__() if a subclass ever
    needs them to differ, the same way FireWeatherUpdater decouples section/product.
    """

    VMAX: float = 1.0             # override: encode_uv's clip range (m/s)
    PALETTES: dict = {}            # override: {name: [(r,g,b), ...]}
    DEFAULT_PALETTE: str = ""      # override: fallback/default palette name
    KEY_TITLE: str = ""            # override: legend key title
    KEY_TICK_FORMAT: str = "%.1f"  # override where a different precision reads better
    # Multiplier applied to VMAX ONLY for the legend key's displayed scale/ticks -- the
    # underlying encode_uv data, particle physics, and frontend VMAX always stay in
    # native m/s. 1.0 (default, e.g. currents) keeps the key in the same units as VMAX.
    # JetStreamUpdater sets 3.6 for a km/h key, mirroring WindUpdater.save_wind_key's
    # identical m/s->km/h key-only rescale: Normalize is linear, so scaling VMAX and the
    # tick positions by the same factor produces identical colours at each fractional
    # position along the bar -- only the axis units/tick labels change.
    KEY_SPEED_SCALE: float = 1.0

    def __init__(self, config: AtmosGLConfig, section_label: str, map_data: MapData):
        super().__init__(config, section_label, map_data)
        # No static PNG -- only the GPU velocity texture.
        self.per_hour_outputs = ["_data.png"]
        self.status_product = self.section

    def _palette(self) -> str:
        name = self.settings.get("palette", self.DEFAULT_PALETTE)
        return name if name in self.PALETTES else self.DEFAULT_PALETTE

    def save_key(self, output_path):
        """Standalone speed colourbar (_key.png). Regenerated each cycle so
        palette/font config changes take effect (no existence guard)."""
        cmap = mcolors.LinearSegmentedColormap.from_list(
            f"{self.status_product}_speed", self.PALETTES[self._palette()], N=256
        )
        vmax_display = self.VMAX * self.KEY_SPEED_SCALE
        norm = mcolors.Normalize(vmin=0.0, vmax=vmax_display)
        ticks = np.linspace(0.0, vmax_display, 4)

        self.save_key_image(
            output_path,
            opaque_cmap(cmap),
            norm,
            ticks,
            self.KEY_TITLE,
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            tick_format=self.KEY_TICK_FORMAT,
            weight="bold",
        )

    def _warm_baseline_cache(self):
        """Warm the shared per-cycle baseline cache (map_data.shared_state) for this
        layer's forecast source. Override to call get_gfs_state() or get_rtofs_state()
        -- render_all_hours resolves its own state from the catalog, so the return
        value is unused; this exists purely for the warming side-effect."""
        raise NotImplementedError(
            f"{type(self).__name__}._warm_baseline_cache() not implemented"
        )

    def plot(self, field0, state):
        """Render this hour's velocity texture. Override per layer."""
        raise NotImplementedError(f"{type(self).__name__}.plot() not implemented")

    def run(self, max_hours=None):
        self._warm_baseline_cache()
        # The legend key is cheap to draw and depends only on palette/key_fontsize
        # settings, not forecast data. Refresh it unconditionally every run, so
        # settings changes apply immediately instead of waiting on should_plot_for_hour's
        # data-freshness gate below.
        self.save_key(self.output_path)
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            self.status_product,
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
            max_hours=max_hours,
        )
