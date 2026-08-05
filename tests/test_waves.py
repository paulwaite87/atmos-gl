#!/usr/bin/env python3
"""Tests for WavesUpdater's land-mask wiring (the caching/coastline-cut logic itself
lives in LandMaskCache -- see tests/test_coastline.py for its own coverage; mirrors
test_currents.py's identical wiring test for CurrentsUpdater)."""
from unittest.mock import MagicMock, patch

from atmos_gl.tasks.waves import WavesUpdater


def test_init_wires_a_land_mask_cache_labelled_waves():
    """Every LandMaskCache consumer (currents, waves) now shares one GSHHG 'h'
    geometry -- see docs/adr/0013 -- so there's no per-caller resolution to assert
    here anymore, just that WavesUpdater wires up its own labelled cache."""
    from atmos_gl.lib.coastline import LandMaskCache

    def fake_updater_init(self, config, section, map_data):
        self.section = section.lower()
        self.settings = {}

    with patch("atmos_gl.tasks.common.Updater.__init__", fake_updater_init):
        u = WavesUpdater(config=MagicMock(), map_data=MagicMock())

    assert isinstance(u._land_mask, LandMaskCache)
    assert u._land_mask._label == "Waves"
