#!/usr/bin/env python3
import os
import logging
import numpy as np
from PIL import Image

from terrascope.lib.config import TerrascopeConfig
from terrascope.lib.gibs import night_lights_cache_path, read_night_lights_date
from .common import Updater, MapData

logger = logging.getLogger(__name__)

# Warm ramp, faint -> bright: deep orange, yellow, near-white.
_RAMP_T = np.array([0.0, 0.55, 1.0])
_RAMP_RGB = np.array([[205, 75, 10], [255, 190, 50], [255, 250, 235]], dtype=np.float64)
# Alpha rises with brightness so dim towns are subtle and cores are solid.
_ALPHA_MIN = 0.35


def ramp_colors(t):
    """RGB (…,3 float) along the warm ramp for t in [0, 1]."""
    return np.stack([np.interp(t, _RAMP_T, _RAMP_RGB[:, i]) for i in range(3)], axis=-1)


def stretch_to_rgba(raw, noise_floor, saturation=255):
    """8-bit GIBS radiance levels (H,W) or (H,W,4) -> transparent warm RGBA (H,W,4).
    Levels <= noise_floor, and pixels the source marks as no-data (alpha 0), are fully
    transparent; above it the level is log-stretched up to `saturation` along the ramp.
    """
    raw = np.asarray(raw)
    if raw.ndim == 3:
        level = raw[..., 0].astype(np.float64)
        valid = raw[..., 3] > 0 if raw.shape[2] > 3 else np.ones(level.shape, bool)
    else:
        level = raw.astype(np.float64)
        valid = np.ones(level.shape, bool)

    span = max(float(saturation) - float(noise_floor), 1.0)
    above = np.clip(level - noise_floor, 0.0, None)
    t = np.clip(np.log1p(above) / np.log1p(span), 0.0, 1.0)

    out = np.zeros(level.shape + (4,), dtype=np.uint8)
    lit = valid & (level > noise_floor)
    out[..., :3] = np.rint(ramp_colors(t)).astype(np.uint8)
    alpha = (_ALPHA_MIN + (1.0 - _ALPHA_MIN) * t) * 255.0
    out[..., 3] = np.where(lit, np.rint(alpha), 0).astype(np.uint8)
    out[~lit, :3] = 0
    return out


class NightLightsUpdater(Updater):
    def __init__(self, config: TerrascopeConfig, map_data: MapData):
        super().__init__(config, "Night_Lights", map_data)
        # The data_collector owns the GIBS download; we read the single global cache it
        # maintains (and the date sidecar next to it) and render the transparent overlay.
        self.cache_output_path = night_lights_cache_path(self.workdir)

    def _signature(self) -> str:
        return self._settings_signature(
            {"noise_floor": self.settings.get("noise_floor", 40)}
        )

    def render(self):
        with Image.open(self.cache_output_path) as raw:
            data = np.array(raw.convert("RGBA"))
        rgba = stretch_to_rgba(
            data,
            noise_floor=float(self.settings.get("noise_floor", 40)),
        )
        Image.fromarray(rgba, "RGBA").save(self.output_path, "PNG")
        date = read_night_lights_date(self.workdir)
        if date:
            self._write_meta_sidecar("night_lights_meta.json", "date", date)

    def run(self, max_hours=None):
        """Render the overlay from the collector-maintained cache. No network.
        Re-renders only when the cache is newer than the output or the styling settings
        changed. max_hours is accepted only for layer_builder's uniform dispatch."""
        if not os.path.exists(self.cache_output_path):
            logger.info("Night lights: cache not present yet; skipping.")
            return
        sig = self._signature()
        out = self.output_path
        if self._is_render_fresh(out, [self.cache_output_path], sig):
            logger.debug("Night lights: output already up to date; skipping.")
            return
        self.render()
        self._write_render_signature(out, sig)
