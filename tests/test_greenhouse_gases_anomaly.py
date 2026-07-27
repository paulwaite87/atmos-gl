#!/usr/bin/env python3
"""Pure-math tests for the greenhouse-gas anomaly computation: no network, no files,
no netCDF -- just numpy arrays in and out. See docs/adr context in the GHG design
grill: the baseline (CAMS EGG4, coarse) is upsampled onto the current field's (GEOS-CF,
fine) grid, not the other way round, so Absolute and Anomaly renders never show a
visible resolution jump when a user switches modes.
"""
import numpy as np

from atmos_gl.lib.greenhouse_gases import compute_anomaly, regrid_onto, resolve_baseline_year


def test_regrid_onto_interpolates_baseline_onto_target_grid():
    # A simple linear field on a coarse 2-point grid: value == lon.
    coarse_lats = np.array([0.0, 10.0])
    coarse_lons = np.array([0.0, 10.0])
    coarse_field = np.array([[0.0, 10.0], [0.0, 10.0]])

    fine_lats = np.array([0.0, 5.0, 10.0])
    fine_lons = np.array([0.0, 5.0, 10.0])

    result = regrid_onto(coarse_field, coarse_lats, coarse_lons, fine_lats, fine_lons)

    assert result.shape == (3, 3)
    # Linear interpolation of value==lon onto lon=[0, 5, 10] gives [0, 5, 10] per row.
    np.testing.assert_allclose(result[0], [0.0, 5.0, 10.0])
    np.testing.assert_allclose(result[1], [0.0, 5.0, 10.0])


def test_compute_anomaly_subtracts_regridded_baseline_from_current():
    lats = np.array([0.0, 10.0])
    lons = np.array([0.0, 10.0])
    current = np.array([[5.0, 5.0], [5.0, 5.0]])
    baseline = np.array([[2.0, 2.0], [2.0, 2.0]])

    anomaly = compute_anomaly(current, lats, lons, baseline, lats, lons)

    np.testing.assert_allclose(anomaly, [[3.0, 3.0], [3.0, 3.0]])


def test_compute_anomaly_output_matches_current_finer_grid_not_baseline_coarser_grid():
    # Baseline lives on a coarse 2x2 grid; current lives on a finer 3x3 grid.
    coarse_lats = np.array([0.0, 10.0])
    coarse_lons = np.array([0.0, 10.0])
    baseline = np.array([[1.0, 1.0], [1.0, 1.0]])

    fine_lats = np.array([0.0, 5.0, 10.0])
    fine_lons = np.array([0.0, 5.0, 10.0])
    current = np.full((3, 3), 4.0)

    anomaly = compute_anomaly(current, fine_lats, fine_lons, baseline, coarse_lats, coarse_lons)

    # Output shape follows the CURRENT (finer) grid -- the baseline was upsampled onto
    # it, not the current field downsampled onto the baseline's coarser grid.
    assert anomaly.shape == (3, 3)
    np.testing.assert_allclose(anomaly, np.full((3, 3), 3.0))


# --- resolve_baseline_year: shared by CamsEgg4BaselineCollector and GhgUpdater so the
# two never drift on what "the configured year" means. ---


def test_resolve_baseline_year_passes_through_a_year_in_range():
    assert resolve_baseline_year({"baseline_year": 2015}) == 2015


def test_resolve_baseline_year_clamps_above_egg4_coverage():
    assert resolve_baseline_year({"baseline_year": 2099}) == 2020


def test_resolve_baseline_year_clamps_below_egg4_coverage():
    assert resolve_baseline_year({"baseline_year": 1990}) == 2003


def test_resolve_baseline_year_defaults_to_the_minimum_when_unset():
    assert resolve_baseline_year({}) == 2003


def test_resolve_baseline_year_falls_back_to_the_minimum_when_unparseable():
    assert resolve_baseline_year({"baseline_year": "not-a-year"}) == 2003
