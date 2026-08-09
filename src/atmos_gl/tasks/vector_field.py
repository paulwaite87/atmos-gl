#!/usr/bin/env python3
"""Shared base for particle-only vector-field layers -- a per-hour u/v velocity
texture (encode_uv, no static heatmap PNG). Currently CurrentsUpdater and
JetStreamUpdater.

Extracted per CLAUDE.md's "Prefer Common Code" directive: JetStreamUpdater turned out
to need CurrentsUpdater's exact shape (no heatmap, fixed VMAX), not WindUpdater's
(heatmap + particles, per-run dynamic VMAX pre-scan) -- the original scoping guess
before comparing the two directly.

Deepens the template-method pattern already established elsewhere in tasks/ (see
CLAUDE.md's "Deepening Template-Method Hierarchies"): this base owns run()'s control
flow; each subclass sets VMAX and implements plot() (the per-layer field processing,
ending in an encode_uv call) and _warm_baseline_cache() (which forecast source -- GFS
or RTOFS -- this layer's run comes from).

The palette + legend key are entirely client-side now (issue #302) -- colour is
applied to the velocity texture in the browser (ui/modules/currents.js/jetstream.js's
own PALETTES/buildLUT), so this base no longer needs to know about palettes at all.
"""
import logging

from atmos_gl.lib.config import AtmosGLConfig
from .common import Updater, MapData, MultiHourRenderMixin

logger = logging.getLogger(__name__)


class VectorFieldUpdater(Updater, MultiHourRenderMixin):
    """Base for a per-hour u/v velocity-texture-only layer. self.status_product
    defaults to self.section (the common case for every current consumer); override
    it post-super().__init__() if a subclass ever needs them to differ, the same way
    FireWeatherUpdater decouples section/product.
    """

    VMAX: float = 1.0             # override: encode_uv's clip range (m/s)

    def __init__(self, config: AtmosGLConfig, section_label: str, map_data: MapData):
        super().__init__(config, section_label, map_data)
        # No static PNG -- only the GPU velocity texture.
        self.per_hour_outputs = ["_data.png"]
        self.status_product = self.section

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
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            self.status_product,
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
            max_hours=max_hours,
        )
