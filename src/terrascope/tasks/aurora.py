#!/usr/bin/env python3
"""Aurora layer rendering (Updater): AuroraCollector's OVATION probability grid
(lib/aurora.py) encoded as a raw data texture for ui/modules/aurora.js, issue #312's
client-side-palette convention -- the colour ramp, the cutoff and opacity are applied
in the browser, so none of them re-renders anything here.

The probability (0-100) is encoded over a FIXED 0-100 domain, never rescaled to the
current maximum: a quiet night peaks around 15%, and rescaling would make it look like
a major storm. The feed's grid (lat -90..90 south first, lon 0..359) is flipped north
first, and rolled onto the project's -180..179 longitude axis, which is the layout the
GPU fill texture needs. A sidecar carries the grid's observation and forecast times for
the legend.
"""
import logging
import os

import numpy as np

from terrascope.lib.aurora import aurora_grid_cache_path, load_aurora_grid
from terrascope.lib.config import TerrascopeConfig
from terrascope.lib.texture import encode_frames
from .common import MapData, Updater

logger = logging.getLogger(__name__)

# Probability, percent. Mirrored by ui/modules/aurora.js's ENCODE_DOMAIN.
ENCODE_DOMAIN = (0.0, 100.0)


def texture_field(probability: np.ndarray) -> np.ndarray:
    """The feed's (lat -90..90, lon 0..359) grid as a (north-first, lon -180..179) field."""
    rolled = np.roll(probability, 180, axis=1)  # feed lon 180 -> column 0 (= -180)
    return rolled[::-1].astype(np.float32)


class AuroraUpdater(Updater):
    def __init__(self, config: TerrascopeConfig, map_data: MapData):
        super().__init__(config, "aurora", map_data)

    def run(self, max_hours=None):
        # max_hours is a no-op: one latest grid, nothing per forecast hour.
        grid_path = aurora_grid_cache_path(self.workdir)
        if not os.path.exists(grid_path):
            return
        # Nothing render-relevant is configurable (the palette is client-side), so the
        # data's mtime is the whole freshness check.
        sig = self._settings_signature({})
        if self._is_render_fresh(self.output_path, [grid_path], sig):
            return
        probability, observed, forecast = load_aurora_grid(grid_path)
        encode_frames([texture_field(probability)], self.output_path, *ENCODE_DOMAIN)
        self._write_meta_sidecar(
            "aurora_meta.json", "aurora", {"observed": observed, "forecast": forecast}
        )
        self._write_render_signature(self.output_path, sig)
        logger.info(f"{self.section}: rendered aurora texture ({observed}).")
