#!/usr/bin/env python3
import os
import logging

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, encode_uv, smooth_flow_direction

logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class WindUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Wind", map_data)
        self.VMAX_WIND = 40.0
        # Direction-coherence radius (grid cells, ~0.25 deg each) for the advection field:
        # broadens coarse-grid shear seams into gradual turns so particles curve through
        # them instead of dwelling/stalling. Speed (colour) is untouched. 0 disables;
        # ~2 is a good windy-like default. Tunable — raise for broader, softer curves.
        self.FLOW_COHERENCE = float(self.settings.get("flow_coherence_radius", 2.0))
        # Wind has NO static PNG — only the GPU velocity texture.
        self.per_hour_outputs = ["_data.png"]

    def plot(self, field0):
        """Render the per-hour wind velocity texture (R=U east, G=V north).

        Barbs are no longer rendered — wind is shown purely as animated particles on
        the frontend, which advect against this velocity field. The particle shader
        (_windparticles_gl.js) decodes w.rg as (u, v) via `w.rg * (2*vmax) - vmax`,
        which is exactly what encode_uv writes.

        Per-hour file ({base}_f{NNN}_data.png) — the frontend scrubber fetches the
        hour it needs directly.
        """
        u = field0["u"]  # m/s
        v = field0["v"]  # m/s

        # Manufacture the gradual cross-shear bending the coarse grid lacks: smooth the
        # flow DIRECTION (speed/colour untouched) so particles curve through boundaries
        # instead of forming hard seams between independently-moving regions.
        u, v = smooth_flow_direction(u, v, self.FLOW_COHERENCE)

        out_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", self.VMAX_WIND, lat=field0.get("lat"))
        logger.info(
            f"Finished Wind velocity texture f{int(self.forecast_hour_str):03d} (R=U, G=V)."
        )

    def run(self):
        self.get_gfs_state()
        # Render every available forecast hour's velocity texture (gap-filling).
        self.render_all_hours(
            "wind",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
        )