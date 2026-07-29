#!/usr/bin/env python3
"""Tests for air_quality.py's closed-form Web Mercator Y pre-warp (plot()'s
merc_y = _MERCATOR_R * log(tan(pi/4 + lat/2)) line) -- verifies the formula's
_MERCATOR_R sphere radius actually matches ccrs.Mercator.GOOGLE's own projection,
since plot() relies on this instead of a real cartopy/PROJ transform. Pure math,
no matplotlib figure or netCDF data involved -- see plot()'s Mercator pre-warp
comment for why the rest of that method isn't unit-tested.
"""
import cartopy.crs as ccrs
import numpy as np
import pytest

from atmos_gl.tasks.air_quality import _MERCATOR_R
from atmos_gl.tasks.plotting import MERCATOR_LAT_LIMIT


def _closed_form_merc_y(lat_deg):
    return _MERCATOR_R * np.log(np.tan(np.pi / 4 + np.radians(lat_deg) / 2))


def test_closed_form_merc_y_matches_cartopy_google_mercator():
    for lat_deg in (-80.0, -45.0, -10.0, 0.0, 10.0, 45.0, 80.0):
        _, expected_y = ccrs.Mercator.GOOGLE.transform_point(0.0, lat_deg, ccrs.PlateCarree())

        assert _closed_form_merc_y(lat_deg) == pytest.approx(expected_y, rel=1e-6, abs=1e-6)


def test_closed_form_merc_y_matches_cartopy_at_the_mercator_lat_limit():
    for lat_deg in (-MERCATOR_LAT_LIMIT, MERCATOR_LAT_LIMIT):
        _, expected_y = ccrs.Mercator.GOOGLE.transform_point(0.0, lat_deg, ccrs.PlateCarree())

        assert _closed_form_merc_y(lat_deg) == pytest.approx(expected_y, rel=1e-6, abs=1e-6)
