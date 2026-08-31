#!/usr/bin/env python3
"""Flood Risk layer rendering (Updater) -- see issue #371.

Historical and Live are two INDEPENDENTLY-SOURCED metrics, not two views of the
same data (see the design grill in issue #371): Live's `band` is GloFAS
return-period SEVERITY (0..len(RETURN_PERIODS_YEARS), which forecast-exceedance
tier a cell is in); Historical's `band` is JRC's depth-HAZARD category (0..4,
JRC's own reclass scale, see lib/flood_risk.py). Each therefore gets its own
encode domain (_LIVE_ENCODE_DOMAIN / _HISTORICAL_ENCODE_DOMAIN) -- the two are
not comparable on one shared scale.

Both render as a raw, un-colored data texture (issue #312's client-side-palette
convention, matching greenhouse_gases/air_quality) -- there is no separate static
contourf PNG for either mode, since contourf's smooth interpolation between
levels is the wrong visual for discrete category data in the first place, and
this layer's OUTFILES entry (a plain ".png") is therefore itself the texture,
exactly like greenhouse_gases's own canonical output.

Both variants render EVERY cycle, independent of the configured mode -- mirrors
GhgUpdater's "render everything, publish only what's selected" pattern, so
switching modes in the UI is instant rather than waiting for a fresh render.
Live renders into its own "_live" variant path (not self.output_path directly)
while it's being kept warm in the background, exactly so a Historical-mode
publish at the end of run() can't be clobbered by a same-cycle Live re-render (or
vice-versa) -- see run()'s output_path swap.
"""
import logging
import os

import numpy as np

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.flood_risk import (
    RETURN_PERIODS_YEARS,
    jrc_hazard_mosaic_cache_path,
    load_jrc_hazard_mosaic,
)
from atmos_gl.lib.texture import encode_frames
from .common import ForecastState, MapData, MultiHourRenderMixin, Updater

logger = logging.getLogger(__name__)

_LIVE_PRODUCT = "flood_risk_live"

# Live's band is a 1-based tier index into RETURN_PERIODS_YEARS (0 = below the
# lowest tier) -- derived from that same source of truth rather than hardcoded, so
# an edit to the tier list can't silently desync the encode domain from what
# ensemble_severity_band() actually produces.
_LIVE_ENCODE_DOMAIN = (0.0, float(len(RETURN_PERIODS_YEARS)))
# Historical's band is JRC's own reclass scale (1:<1m .. 4:>10m) plus this
# mosaic's own 0 ("no known hazard" / outside any tile's footprint) -- see
# lib/flood_risk.py's resample_jrc_tile_onto_grid docstring.
_HISTORICAL_ENCODE_DOMAIN = (0.0, 4.0)


class FloodRiskUpdater(Updater, MultiHourRenderMixin):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "flood_risk", map_data)
        self.mode = self.settings.get("mode", "live").strip().lower()
        # Reported progress reflects whatever mode is actually configured: Live's
        # per-forecast-hour bar when live, else the Historical single-shot
        # decaying-freshness fallback (status_product=None, Updater's default) --
        # matches "status should describe what the user is looking at".
        self.status_product = _LIVE_PRODUCT if self.mode == "live" else None

    def _variant_path(self, suffix: str) -> str:
        base, ext = os.path.splitext(self.output_path)
        return f"{base}_{suffix}{ext}"

    def _plot_live(self, field0, state: ForecastState):
        """Render one forecast hour's severity-band texture. Consumes the field
        render_all_hours already fetched (matching ScalarFieldUpdater.plot's own
        contract) -- `values2` (exceedance fraction) isn't encoded yet, deferred to
        the frontend hover/legend work."""
        out = self.get_output_path_for_hour(state.fhour)
        encode_frames([field0["values"]], out, *_LIVE_ENCODE_DOMAIN)
        logger.info(f"{self.section}: rendered live severity texture f{state.fhour:03d}.")

    def _render_historical(self) -> str | None:
        """Render the static JRC hazard mosaic's data texture into its own "_historical"
        variant path, if the mosaic is cached and the render isn't already fresh (the
        mosaic itself never changes once FloodRiskHistoricalCollector finishes it, so
        this effectively renders once, ever). Returns the variant path, or None if the
        mosaic isn't cached yet (the historical collector hasn't finished downloading
        all 271 tiles)."""
        mosaic_path = jrc_hazard_mosaic_cache_path(self.workdir)
        if not os.path.exists(mosaic_path):
            return None

        out = self._variant_path("historical")
        sig = self._settings_signature({})
        if not self._is_render_fresh(out, [mosaic_path], sig):
            band, _lat, _lon = load_jrc_hazard_mosaic(mosaic_path)
            encode_frames([band.astype(np.float32)], out, *_HISTORICAL_ENCODE_DOMAIN)
            self._write_render_signature(out, sig)
            logger.info(f"{self.section}: rendered historical hazard texture.")
        return out

    def run(self, max_hours=None):
        base_output_path = self.output_path

        # --- Live: render every available forecast hour, into its OWN stable
        # variant name -- swapped in only for this call, so render_all_hours'
        # internal publish_current_hour (which always targets self.output_path)
        # can't clobber a Historical-mode publish below. Runs unconditionally of
        # the configured mode (see module docstring).
        self.output_path = self._variant_path("live")
        self.render_all_hours(
            _LIVE_PRODUCT,
            plot_fn=self._plot_live,
            field_ready=lambda f: f.get("values") is not None,
            max_hours=max_hours,
        )
        live_out = self.output_path
        self.output_path = base_output_path

        # --- Historical: render the static JRC mosaic, also unconditional of mode.
        historical_out = self._render_historical()

        # --- Publish whichever mode is currently configured to the stable,
        # run-agnostic base filename the frontend reads.
        if self.mode == "historical":
            if historical_out:
                self._publish_variant(historical_out)
        elif os.path.exists(live_out):
            self._publish_variant(live_out)
