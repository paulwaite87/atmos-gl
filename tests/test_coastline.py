#!/usr/bin/env python3
"""Tests for lib/coastline.py's LandMaskCache and nearest_fill_and_regrid_uv --
architecture review candidate "share currents' and waves' land-mask/regrid pipeline".
CurrentsUpdater._land_mask_for and WavesUpdater._land_mask_for were byte-identical
(waves.py's own docstring said so: "Mirrors currents.py's _land_mask_for exactly"),
as was the nearest-fill-then-regrid block ahead of each class's land-mask cut. These
tests lock the shared behavior directly; each caller's own test file only needs to
confirm it's wired up, not re-test the logic.
"""
from unittest.mock import MagicMock, patch, call

import numpy as np

from atmos_gl.lib.coastline import LandMaskCache, nearest_fill_and_regrid_uv


# ---------------------------------------------------------------------------
# LandMaskCache
# ---------------------------------------------------------------------------

def test_get_caches_per_shape():
    cache = LandMaskCache("Test")
    sentinel = np.array([[True, False]])
    with patch(
        "atmos_gl.lib.coastline.coastline_land_mask", return_value=sentinel
    ) as mock_coast:
        first = cache.get(lat=[0.0], lon=[0.0, 1.0], shape=(1, 2))
        second = cache.get(lat=[0.0], lon=[0.0, 1.0], shape=(1, 2))

    assert first is sentinel
    assert second is sentinel
    mock_coast.assert_called_once()


def test_get_uses_a_separate_cache_entry_per_distinct_shape():
    cache = LandMaskCache("Test")
    with patch(
        "atmos_gl.lib.coastline.coastline_land_mask",
        side_effect=[np.array([[True]]), np.array([[False, False]])],
    ) as mock_coast:
        cache.get(lat=[0.0], lon=[0.0], shape=(1, 1))
        cache.get(lat=[0.0], lon=[0.0, 1.0], shape=(1, 2))

    assert mock_coast.call_count == 2


def test_get_passes_the_global_bbox_and_configured_resolution():
    cache = LandMaskCache("Test", res="10m")
    with patch(
        "atmos_gl.lib.coastline.coastline_land_mask", return_value=None
    ) as mock_coast:
        cache.get(lat=[0.0], lon=[0.0], shape=(1, 1))

    args, kwargs = mock_coast.call_args
    assert args[2:] == (-180.0, -90.0, 180.0, 90.0)
    assert kwargs["res"] == "10m"


def test_get_defaults_resolution_to_50m():
    cache = LandMaskCache("Test")
    with patch(
        "atmos_gl.lib.coastline.coastline_land_mask", return_value=None
    ) as mock_coast:
        cache.get(lat=[0.0], lon=[0.0], shape=(1, 1))

    assert mock_coast.call_args.kwargs["res"] == "50m"


def test_get_caches_none_too_when_geometry_is_unavailable():
    """Matches the pre-extraction behavior exactly: a None result (geometry load
    failure) is cached like any other value, so a shape that failed once doesn't
    retry every subsequent call within the same run."""
    cache = LandMaskCache("Test")
    with patch(
        "atmos_gl.lib.coastline.coastline_land_mask", return_value=None
    ) as mock_coast:
        first = cache.get(lat=[0.0], lon=[0.0], shape=(1, 1))
        second = cache.get(lat=[0.0], lon=[0.0], shape=(1, 1))

    assert first is None
    assert second is None
    mock_coast.assert_called_once()


# ---------------------------------------------------------------------------
# nearest_fill_and_regrid_uv
# ---------------------------------------------------------------------------

def make_regrid_fn():
    """A fake regrid_for_lod: returns fixed new_lats/new_lons and the field
    UNCHANGED, so tests can inspect exactly what was passed in (post-nearest-fill)."""
    calls = []

    def regrid_fn(field, lats, lons, fill_value=np.nan, step_override=None):
        calls.append({"field": field.copy(), "step_override": step_override})
        return np.array([0.0, 1.0]), np.array([0.0, 1.0]), field

    return regrid_fn, calls


def test_regrids_both_u_and_v_with_the_given_step_override():
    regrid_fn, calls = make_regrid_fn()
    u = np.array([[1.0, 2.0]], dtype=np.float32)
    v = np.array([[3.0, 4.0]], dtype=np.float32)

    nearest_fill_and_regrid_uv(regrid_fn, u, v, [0.0], [0.0, 1.0], step_deg=0.08)

    assert len(calls) == 2
    assert calls[0]["step_override"] == 0.08
    assert calls[1]["step_override"] == 0.08


def test_nearest_fills_native_nan_before_regridding():
    regrid_fn, calls = make_regrid_fn()
    u = np.array([[1.0, np.nan, 1.0]], dtype=np.float32)
    v = np.array([[2.0, np.nan, 2.0]], dtype=np.float32)

    nearest_fill_and_regrid_uv(regrid_fn, u, v, [0.0], [0.0, 1.0, 2.0], step_deg=0.08)

    # regrid_fn must never see a NaN -- it was nearest-filled first.
    assert not np.isnan(calls[0]["field"]).any()
    assert not np.isnan(calls[1]["field"]).any()


def test_all_nan_native_is_left_unfilled():
    """distance_transform_edt needs SOME valid data to fill from -- an entirely-NaN
    field is left as-is (matches the original `bad.any() and not bad.all()` guard)."""
    regrid_fn, calls = make_regrid_fn()
    u = np.full((1, 2), np.nan, dtype=np.float32)
    v = np.full((1, 2), np.nan, dtype=np.float32)

    nearest_fill_and_regrid_uv(regrid_fn, u, v, [0.0], [0.0, 1.0], step_deg=0.08)

    assert np.isnan(calls[0]["field"]).all()
    assert np.isnan(calls[1]["field"]).all()


def test_does_not_mutate_the_callers_original_arrays():
    regrid_fn, _ = make_regrid_fn()
    u = np.array([[1.0, np.nan]], dtype=np.float32)
    v = np.array([[2.0, np.nan]], dtype=np.float32)
    u_original = u.copy()
    v_original = v.copy()

    nearest_fill_and_regrid_uv(regrid_fn, u, v, [0.0], [0.0, 1.0], step_deg=0.08)

    np.testing.assert_array_equal(u, u_original, err_msg="input u must be untouched", strict=True)
    np.testing.assert_array_equal(v, v_original, err_msg="input v must be untouched", strict=True)


def test_returns_new_lats_new_lons_and_both_regridded_fields():
    regrid_fn, _ = make_regrid_fn()
    u = np.array([[1.0, 2.0]], dtype=np.float32)
    v = np.array([[3.0, 4.0]], dtype=np.float32)

    new_lats, new_lons, out_u, out_v = nearest_fill_and_regrid_uv(
        regrid_fn, u, v, [0.0], [0.0, 1.0], step_deg=0.08
    )

    np.testing.assert_array_equal(new_lats, [0.0, 1.0])
    np.testing.assert_array_equal(new_lons, [0.0, 1.0])
    np.testing.assert_array_equal(out_u, u)
    np.testing.assert_array_equal(out_v, v)
