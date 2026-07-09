#!/usr/bin/env python3
"""Tests for the pure numeric transforms in lib/unpack.py (architecture review
candidate "lock down the numeric core with tests"). _swell_uv, _standardize_lon, and
_regrid_curvilinear had zero coverage before this -- _swell_uv is where the real
waves_data_unpack sign-flip bug lived (see CONTEXT.md's "Direction convention (FROM)").
The GRIB-opening glue in each *_data_unpack function stays untested here; it needs real
cfgrib to exercise honestly and carries little bug risk compared to this math.
"""
import numpy as np
import pytest

from atmos_gl.lib.unpack import _swell_uv, _standardize_lon, _regrid_curvilinear


# ---- _swell_uv --------------------------------------------------------------

def test_swell_uv_from_north_heads_south():
    """direction=0 (FROM due north) -> travels south -> v is NEGATIVE. This is the
    exact convention the real bug got backwards."""
    u, v, mag = _swell_uv(swh=np.array([2.0]), mwd=np.array([0.0]))
    assert u[0] == pytest.approx(0.0, abs=1e-5)
    assert v[0] == pytest.approx(-2.0)


def test_swell_uv_from_east_heads_west():
    """direction=90 (FROM due east) -> travels west -> u is NEGATIVE."""
    u, v, mag = _swell_uv(swh=np.array([3.0]), mwd=np.array([90.0]))
    assert u[0] == pytest.approx(-3.0)
    assert v[0] == pytest.approx(0.0, abs=1e-5)


def test_swell_uv_preserves_magnitude():
    swh = np.array([1.5, 4.0, 7.25])
    mwd = np.array([30.0, 145.0, 300.0])
    u, v, mag = _swell_uv(swh, mwd)
    assert np.allclose(np.hypot(u, v), swh)
    assert np.allclose(mag, swh)


@pytest.mark.parametrize(
    "swh,mwd",
    [
        (np.array([-1.0]), np.array([10.0])),   # negative height
        (np.array([61.0]), np.array([10.0])),   # above the 60m clip
        (np.array([np.nan]), np.array([10.0])), # non-finite height
        (np.array([2.0]), np.array([np.nan])),  # non-finite direction
    ],
)
def test_swell_uv_masks_bad_cells_to_nan(swh, mwd):
    u, v, mag = _swell_uv(swh, mwd)
    assert np.isnan(u[0])
    assert np.isnan(v[0])
    assert np.isnan(mag[0])


# ---- _standardize_lon --------------------------------------------------------

def test_standardize_lon_wraps_above_180():
    lons = np.array([170.0, 200.0, -10.0, 0.0])
    field = np.array([[1.0, 2.0, 3.0, 4.0]])
    lons_sorted, (field_sorted,) = _standardize_lon(lons, field)
    assert 200.0 not in lons_sorted
    assert np.any(np.isclose(lons_sorted, -160.0))  # 200 wrapped to -160


def test_standardize_lon_sorts_ascending():
    lons = np.array([170.0, 200.0, -10.0, 0.0])
    field = np.array([[1.0, 2.0, 3.0, 4.0]])
    lons_sorted, _ = _standardize_lon(lons, field)
    assert list(lons_sorted) == sorted(lons_sorted.tolist())


def test_standardize_lon_reorders_field_columns_consistently():
    lons = np.array([10.0, -10.0])
    field = np.array([[100.0, 200.0]])  # column0 -> lon 10, column1 -> lon -10
    lons_sorted, (field_sorted,) = _standardize_lon(lons, field)
    assert lons_sorted[0] == -10.0
    assert field_sorted[0, 0] == 200.0  # the -10 column's value moved with it
    assert lons_sorted[1] == 10.0
    assert field_sorted[0, 1] == 100.0


def test_standardize_lon_passes_through_none_fields():
    lons = np.array([10.0, -10.0])
    _, (a, b) = _standardize_lon(lons, None, np.array([[1.0, 2.0]]))
    assert a is None
    assert b is not None


# ---- _regrid_curvilinear ------------------------------------------------------

def _dense_source_grid(value=3.0, n=9, span=4.0):
    lat_vals = np.linspace(-span, span, n)
    lon_vals = np.linspace(-span, span, n)
    lat2d, lon2d = np.meshgrid(lat_vals, lon_vals, indexing="ij")
    field = np.full_like(lat2d, value)
    return lat2d, lon2d, field


def test_regrid_curvilinear_is_north_first():
    lat2d, lon2d, u = _dense_source_grid()
    tlat, tlon, out = _regrid_curvilinear(
        lat2d, lon2d, {"u": u}, step=1.0, lat_min=-3.0, lat_max=3.0, k=4
    )
    assert tlat[0] > tlat[-1]  # row 0 = north, descending
    assert tlon[0] < tlon[-1]  # longitude stays ascending


def test_regrid_curvilinear_constant_field_stays_constant_where_covered():
    lat2d, lon2d, u = _dense_source_grid(value=3.0)
    tlat, tlon, out = _regrid_curvilinear(
        lat2d, lon2d, {"u": u}, step=1.0, lat_min=-3.0, lat_max=3.0, k=4
    )
    mid = out["u"][out["u"].shape[0] // 2, out["u"].shape[1] // 2]
    assert np.isfinite(mid)
    assert mid == pytest.approx(3.0, abs=1e-4)


def test_regrid_curvilinear_no_coverage_beyond_distance_cap():
    # source points clustered tightly in one corner
    lat2d = np.array([[10.0, 10.0], [9.5, 9.5]])
    lon2d = np.array([[10.0, 10.5], [10.0, 10.5]])
    u = np.array([[1.0, 1.0], [1.0, 1.0]])
    tlat, tlon, out = _regrid_curvilinear(
        lat2d, lon2d, {"u": u}, step=1.0, lat_min=-10.0, lat_max=10.0, k=1
    )
    far_lat_idx = int(np.argmin(np.abs(tlat - (-9.0))))
    far_lon_idx = int(np.argmin(np.abs(tlon - (-9.0))))
    assert np.isnan(out["u"][far_lat_idx, far_lon_idx])


def test_regrid_curvilinear_excludes_junk_longitude_column():
    lat2d, lon2d, u = _dense_source_grid(value=3.0)
    lon2d = lon2d.copy()
    u = u.copy()
    lon2d[:, 0] = 999.0     # junk column (lon2d >= 500 is the exclusion rule)
    u[:, 0] = 99999.0       # an obviously-wrong value that must never leak in
    tlat, tlon, out = _regrid_curvilinear(
        lat2d, lon2d, {"u": u}, step=1.0, lat_min=-3.0, lat_max=3.0, k=4
    )
    assert np.nanmax(out["u"]) < 100.0


def test_regrid_curvilinear_excludes_out_of_physical_range_values():
    lat2d, lon2d, u = _dense_source_grid(value=3.0)
    u = u.copy()
    u[0, 0] = 500.0  # |x| >= 100 is excluded by the physical-range filter
    tlat, tlon, out = _regrid_curvilinear(
        lat2d, lon2d, {"u": u}, step=1.0, lat_min=-3.0, lat_max=3.0, k=4
    )
    assert np.nanmax(out["u"]) < 100.0
