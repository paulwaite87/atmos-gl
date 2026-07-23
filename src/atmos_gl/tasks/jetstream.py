#!/usr/bin/env python3
"""Jet Stream: 250mb upper-atmosphere wind (see lib/unpack.py's jetstream_data_unpack
and lib/gfs.py's ATMOS_TARGETS), rendered as a per-hour velocity texture only -- no
heatmap (unlike wind.py, which pairs particles with a speed heatmap). Speed information
lives entirely in the particles themselves on the frontend, colour-coded via PALETTES
below (mirroring currents.py's approach, not wind's flat particle colour), so the key
here is the primary way to read speed at a glance server-side.

Structurally identical to CurrentsUpdater (VMAX-clipped encode_uv texture + selectable-
palette key, no heatmap, no per-run VMAX pre-scan) -- both extend VectorFieldUpdater.
No land masking or regrid here, unlike currents: 250mb wind blows over land and ocean
alike (no land/sea distinction at that altitude), and the field is already at the same
native GFS resolution every other atmospheric layer renders at.
"""
import os
import logging

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.texture import encode_uv
from .common import MapData, ForecastState
from .vector_field import VectorFieldUpdater

logger = logging.getLogger(__name__)


class JetStreamUpdater(VectorFieldUpdater):
    """250mb jet-core wind from GFS (via the fieldstore)."""

    # m/s; a live-decoded GFS run during implementation peaked at ~106 m/s in one hour
    # -- 120 gives headroom above that without being so wide the colour ramp reads flat
    # for typical (well below peak) conditions. See encode_uv's own docstring: "pick it
    # a little above the strongest winds you care about."
    VMAX = 120.0
    # Provisional palette -- cool/electric, distinct from wind's warm windy.com ramp and
    # currents' four options, meant to read as "upper atmosphere" rather than surface
    # weather. Final palette choice/tuning happens in the frontend layer (coordinate
    # with ui/modules/jetstream.js); this just needs to exist so the backend colourbar
    # key renders something coherent. More named options can be added the same way
    # currents' PALETTES grew to four.
    PALETTES = {
        "stratosphere": [
            (0.05, 0.05, 0.35),   # indigo (slow)
            (0.0, 0.65, 0.9),     # electric cyan (moderate)
            (0.85, 0.95, 1.0),    # near-white ice-blue (jet core)
        ],
    }
    DEFAULT_PALETTE = "stratosphere"
    KEY_TITLE = "Jet Stream Speed (m/s)"
    KEY_TICK_FORMAT = "%.0f"  # VMAX=120 -- whole numbers read cleaner than currents' %.1f

    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "Jetstream", map_data)

    def _warm_baseline_cache(self):
        self.get_gfs_state()

    def plot(self, field0, state: ForecastState):
        """Write the per-hour jet-core velocity texture (R=U east, G=V north). No
        regrid, no land mask, no heatmap -- just the raw field, same as the encode_uv
        step in CurrentsUpdater.plot()/WindUpdater.plot()."""
        u = field0["u"]
        v = field0["v"]
        lats = field0.get("lat")

        out_for_hour = self.get_output_path_for_hour(state.fhour)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", self.VMAX, lat=lats)

        logger.info(
            f"Finished Jet Stream velocity texture f{state.fhour:03d} (R=U, G=V)."
        )
