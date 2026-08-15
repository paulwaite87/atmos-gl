#!/usr/bin/env python3
"""Tests for WavesUpdater's land-mask wiring (the caching/coastline-cut logic itself
lives in LandMaskCache -- see tests/test_coastline.py for its own coverage; mirrors
test_currents.py's identical wiring test for CurrentsUpdater)."""
from unittest.mock import MagicMock, patch

import numpy as np

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


# ---- coastal-bleed fix: land mask is dilated before masking u/v -------------------
# See docs/adr/0014-dilate-sst-land-mask-for-linear-filtering-bleed.md -- the heat-fill
# shader's LINEAR-filtered alpha discard (and the bar particle engine's VEL_SAMPLE,
# same pattern) blends across the true coastline edge, so the land cut must be
# dilated by one cell before baking NaN into u/v or colour/motion bleeds onto land.

def test_masked_uv_dilates_land_mask_before_masking():
    # Column 2 is the "true" coastline mask; _masked_uv() dilates it by one cell
    # before cutting, so column 1 is expected to come out NaN too even though the
    # mock mask itself doesn't mark it as land.
    land = np.array([[False, False, True], [False, False, True], [False, False, True]])
    u_arr = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])
    v_arr = u_arr.copy()
    new_lats = np.array([10.0, 5.0, 0.0])
    new_lons = np.array([0.0, 5.0, 10.0])

    u = WavesUpdater.__new__(WavesUpdater)
    u.regrid_for_lod = MagicMock()
    u._land_mask = MagicMock()
    u._land_mask.get.return_value = land

    with patch(
        "atmos_gl.tasks.waves.nearest_fill_and_regrid_uv",
        return_value=(new_lats, new_lons, u_arr.copy(), v_arr.copy()),
    ):
        _, out_u, out_v = u._masked_uv({"u": u_arr, "v": v_arr, "lat": None, "lon": None})

    assert np.isnan(out_u[:, 1:]).all()
    assert not np.isnan(out_u[:, 0]).any()
    assert np.isnan(out_v[:, 1:]).all()
    assert not np.isnan(out_v[:, 0]).any()
