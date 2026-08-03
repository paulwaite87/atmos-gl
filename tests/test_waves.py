#!/usr/bin/env python3
"""Tests for WavesUpdater's land-mask wiring (the caching/coastline-cut logic itself
lives in LandMaskCache -- see tests/test_coastline.py for its own coverage; mirrors
test_currents.py's identical wiring test for CurrentsUpdater)."""
from unittest.mock import MagicMock, patch

from atmos_gl.tasks.waves import WavesUpdater


def test_init_wires_a_land_mask_cache_labelled_waves_at_10m_resolution():
    """res="10m", not LandMaskCache's own "50m" default (which currents.py still
    uses): waves' bars can sit at any precise sub-cell position, so a coarser
    coastline is visible as bars drifting onshore past the basemap's actual
    coastline before the data's own (coarser) land boundary catches them -- found
    live (candidate #7, particle-engine consolidation)."""
    from atmos_gl.lib.coastline import LandMaskCache

    def fake_updater_init(self, config, section, map_data):
        self.section = section.lower()
        self.settings = {}

    with patch("atmos_gl.tasks.common.Updater.__init__", fake_updater_init):
        u = WavesUpdater(config=MagicMock(), map_data=MagicMock())

    assert isinstance(u._land_mask, LandMaskCache)
    assert u._land_mask._label == "Waves"
    assert u._land_mask._res == "10m"
