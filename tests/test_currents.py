#!/usr/bin/env python3
"""Tests for CurrentsUpdater. The palette + legend key are entirely client-side now
(issue #302, see ui/modules/currents.js's own PALETTES/buildLUT) -- VectorFieldUpdater
no longer knows about palettes at all, so the coverage here is limited to what's still
actually on the backend: land mask wiring.
"""
from unittest.mock import MagicMock, patch

from atmos_gl.tasks.currents import CurrentsUpdater


# ---- land mask wiring ------------------------------------------------------------
# The caching/coastline-cut logic itself moved to LandMaskCache (lib/coastline.py,
# shared with WavesUpdater -- see tests/test_coastline.py for its own coverage). This
# just confirms CurrentsUpdater wires one up correctly, not the logic again.

def test_init_wires_a_land_mask_cache_labelled_currents():
    from atmos_gl.lib.coastline import LandMaskCache

    def fake_updater_init(self, config, section, map_data):
        self.section = section.lower()
        self.settings = {}

    with patch("atmos_gl.tasks.common.Updater.__init__", fake_updater_init):
        u = CurrentsUpdater(config=MagicMock(), map_data=MagicMock())

    assert isinstance(u._land_mask, LandMaskCache)
    assert u._land_mask._label == "Currents"
