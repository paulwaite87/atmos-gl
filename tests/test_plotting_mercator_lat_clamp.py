#!/usr/bin/env python3
"""Tests for clamp_lats_to_mercator_limit() -- global GFS/RTOFS grids include the
exact +-90 pole rows by design (see lib/unpack.py's CURRENTS_LAT_MIN/MAX comment),
which is a true mathematical singularity for Mercator (y = R*ln(tan(pi/4 + lat/2))
-> infinity at lat=+-90). PROJ logged an "Invalid latitude" error per point on every
contourf/pcolormesh/contour call across every render -- Plot.get_figure() already
clips the visible axes extent to MERCATOR_LAT_LIMIT, but the underlying data arrays
handed to matplotlib weren't clamped, so this fixes that at the source.
"""
import numpy as np

from atmos_gl.tasks.plotting import clamp_lats_to_mercator_limit, MERCATOR_LAT_LIMIT


def test_clamps_exact_poles_to_just_inside_the_limit():
    lats = np.array([-90.0, -45.0, 0.0, 45.0, 90.0])
    clamped = clamp_lats_to_mercator_limit(lats)
    assert clamped[0] == -MERCATOR_LAT_LIMIT
    assert clamped[-1] == MERCATOR_LAT_LIMIT


def test_leaves_lats_within_the_limit_unchanged():
    lats = np.array([-80.0, -10.0, 0.0, 10.0, 80.0])
    clamped = clamp_lats_to_mercator_limit(lats)
    np.testing.assert_array_equal(clamped, lats)


def test_result_never_exceeds_the_limit_in_either_direction():
    lats = np.linspace(-90.0, 90.0, 181)  # a real GFS-style 1deg grid, poles included
    clamped = clamp_lats_to_mercator_limit(lats)
    assert clamped.min() >= -MERCATOR_LAT_LIMIT
    assert clamped.max() <= MERCATOR_LAT_LIMIT


def test_preserves_shape_for_2d_grids():
    """contourf/pcolormesh require lats/lons/values to stay same-shaped -- clamping
    must not mask/drop rows, only rescale their values."""
    lat2d = np.tile(np.array([-90.0, 0.0, 90.0])[:, None], (1, 4))
    clamped = clamp_lats_to_mercator_limit(lat2d)
    assert clamped.shape == lat2d.shape
