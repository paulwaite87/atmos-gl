#!/usr/bin/env python3
"""Population Density layer rendering (Updater): PopulationDensityCollector's
people-per-km² grid (lib/population_density.py) encoded as a raw data texture for
ui/modules/population_density.js, issue #312's client-side-palette convention --
palette, colour range, threshold and opacity are all applied in the browser, so none
of them re-renders anything here.

Encoded as log10(people/km²): density spans six orders of magnitude, from sparse
farmland to city cores, and a heatmap of it reads on a log scale. Empty cells encode
as NaN (alpha 0), so oceans and uninhabited land stay transparent at any threshold.
"""
import logging
import os

import numpy as np

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.population_density import density_grid_cache_path, load_density_grid
from atmos_gl.lib.texture import encode_frames
from .common import MapData, Updater

logger = logging.getLogger(__name__)

# log10(people/km²): 0.1 to 100,000 -- the densest 0.05deg cells (~30km²) on Earth
# sit a little under 10^5. Mirrored by ui/modules/population_density.js's ENCODE_DOMAIN.
ENCODE_DOMAIN = (-1.0, 5.0)


def log_density(density: np.ndarray) -> np.ndarray:
    """log10 of each cell's density, NaN where nobody lives."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(density > 0, np.log10(density), np.nan).astype(np.float32)


class PopulationDensityUpdater(Updater):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "population_density", map_data)

    def run(self, max_hours=None):
        # max_hours is a no-op: one static grid, nothing per forecast hour (see
        # FloodRiskUpdater.run's identical convention).
        grid_path = density_grid_cache_path(self.workdir)
        if not os.path.exists(grid_path):
            return
        sig = self._settings_signature({})
        if self._is_render_fresh(self.output_path, [grid_path], sig):
            return
        density, _lat, _lon = load_density_grid(grid_path)
        encode_frames([log_density(density)], self.output_path, *ENCODE_DOMAIN)
        self._write_render_signature(self.output_path, sig)
        logger.info(f"{self.section}: rendered density texture.")
