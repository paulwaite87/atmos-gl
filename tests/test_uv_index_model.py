#!/usr/bin/env python3
"""lib/uv_index.py: the pure UV Index model (ozone + cloud + sun position -> UVI) and the
WHO band classifier. Expected values are worked by hand from the published
parameterisation (UVI = 12.5 * mu0^2.42 * (O3/300)^-1.23, cloud factor
1 - 0.75 * cloud^3.4), not recomputed by the code under test."""

from datetime import datetime, timezone

import numpy as np
import pytest

from terrascope.lib.uv_index import UV_MIN_VISIBLE, uv_band, uv_index

EQUINOX_NOON = datetime(
    2026, 3, 20, 12, 7, tzinfo=timezone.utc
)  # sun ~overhead at 0N 0E
JUNE_NOON = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
DEC_NOON = datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc)


def at(lat, lon, when, ozone=300.0, cloud=0.0):
    out = uv_index(
        np.array([[ozone]]), np.array([[cloud]]), np.array([lat]), np.array([lon]), when
    )
    return float(out[0, 0])


def test_the_sun_overhead_at_300_du_gives_the_reference_12_5():
    assert at(0.0, 0.0, EQUINOX_NOON) == pytest.approx(12.5, abs=0.15)


def test_less_ozone_means_more_uv():
    assert at(0.0, 0.0, EQUINOX_NOON, ozone=200.0) == pytest.approx(20.6, abs=0.3)
    assert at(0.0, 0.0, EQUINOX_NOON, ozone=400.0) == pytest.approx(8.9, abs=0.2)


def test_the_night_side_is_zero():
    assert at(0.0, 180.0, EQUINOX_NOON) == 0.0


def test_midsummer_noon_at_40_north_is_very_high():
    assert at(40.0, 0.0, JUNE_NOON) == pytest.approx(11.3, abs=0.4)


def test_midwinter_noon_at_60_north_is_barely_above_zero():
    assert at(60.0, 0.0, DEC_NOON) == pytest.approx(0.06, abs=0.04)


def test_overcast_cuts_uv_to_a_quarter_and_half_cloud_barely_matters():
    clear = at(0.0, 0.0, EQUINOX_NOON)
    assert at(0.0, 0.0, EQUINOX_NOON, cloud=100.0) == pytest.approx(
        clear * 0.25, rel=0.02
    )
    assert at(0.0, 0.0, EQUINOX_NOON, cloud=50.0) == pytest.approx(
        clear * 0.929, rel=0.02
    )


def test_it_computes_a_whole_grid_at_once():
    lats, lons = np.array([-10.0, 0.0, 10.0]), np.array([0.0, 90.0, 180.0, 270.0])
    out = uv_index(np.full((3, 4), 300.0), np.zeros((3, 4)), lats, lons, EQUINOX_NOON)
    assert out.shape == (3, 4)
    assert out[1, 0] > 12 and out[1, 2] == 0.0


def test_a_missing_ozone_or_cloud_value_gives_nan_not_a_wrong_number():
    out = uv_index(
        np.array([[np.nan, 300.0]]),
        np.array([[0.0, np.nan]]),
        np.array([0.0]),
        np.array([0.0, 1.0]),
        EQUINOX_NOON,
    )
    assert np.isnan(out).all()


@pytest.mark.parametrize(
    "uvi, band",
    [
        (0.99, None),
        (1.0, "Low"),
        (2.99, "Low"),
        (3.0, "Moderate"),
        (5.99, "Moderate"),
        (6.0, "High"),
        (7.99, "High"),
        (8.0, "Very high"),
        (10.99, "Very high"),
        (11.0, "Extreme"),
        (16.0, "Extreme"),
    ],
)
def test_uv_band_follows_the_who_thresholds_and_hides_below_one(uvi, band):
    assert uv_band(uvi) == band


def test_the_visibility_threshold_is_one():
    assert UV_MIN_VISIBLE == 1.0
