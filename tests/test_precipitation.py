#!/usr/bin/env python3
"""Tests for PrecipitationUpdater.save_precipitation_key -- verifies it matches the
key style shared by every other layer's key (sst/currents/waves/scalar_field), since
this key used to be smaller/unbolded with no explicit tick_format, which is why it
visibly looked different from those."""
from unittest.mock import MagicMock

import numpy as np

from atmos_gl.tasks.precipitation import PrecipitationUpdater


def make_bare_updater(settings=None, common=None):
    """Bypass Updater.__init__ (does config/IO) and wire only what
    save_precipitation_key reads."""
    u = PrecipitationUpdater.__new__(PrecipitationUpdater)
    u.settings = settings or {}
    u.common = common or {}
    # ListedColormap needs at least as many colors as BoundaryNorm's bins (5, from
    # save_precipitation_key's 6 fixed key_ticks) -- matches the real "standard"
    # palette's 7 colors, just not the exact values (irrelevant to what's under test).
    u.PALETTES = {
        "standard": [(0.0, 1.0, 1.0), (0.0, 0.5, 1.0), (0.0, 1.0, 0.0),
                     (1.0, 1.0, 0.0), (1.0, 0.5, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0)],
    }
    u.save_key_image = MagicMock()
    return u


def test_save_precipitation_key_matches_the_shared_key_style():
    u = make_bare_updater()

    u.save_precipitation_key("/tmp/out/precipitation.png")

    u.save_key_image.assert_called_once()
    key_args = u.save_key_image.call_args
    assert key_args.args[0] == "/tmp/out/precipitation.png"
    assert key_args.args[3] == [0.1, 1.0, 5.0, 15.0, 50.0, 100.0]
    assert key_args.args[4] == "Precipitation (mm/hr)"
    assert key_args.kwargs["key_fontsize"] == 10
    assert key_args.kwargs["labelsize"] == 8
    assert key_args.kwargs["weight"] == "bold"
    assert key_args.kwargs["tick_format"] == "%.1f"


def test_save_precipitation_key_honours_a_configured_key_fontsize():
    """key_fontsize is a shared common.key_fontsize setting, not this layer's own
    section (issue: consolidate the 11 per-layer key_fontsize settings)."""
    u = make_bare_updater(common={"key_fontsize": 14})

    u.save_precipitation_key("/tmp/out/precipitation.png")

    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 14


def make_floor_updater(sigma_cells=1.2):
    u = PrecipitationUpdater.__new__(PrecipitationUpdater)
    u.MEANINGFUL_PRECIP_MM_HR = 0.1
    u._smooth_sigma_cells = sigma_cells
    return u


def test_apply_meaningful_floor_zeroes_noise_far_from_any_real_core():
    """Widespread low-level noise (e.g. blur/quantization residue) with no real
    precipitation core anywhere nearby must be zeroed entirely -- a value-only
    threshold (plain clip or smoothstep) can't tell this apart from a real core's own
    falloff, since both live in the same value range; connectivity to an actual
    >=floor core is what distinguishes them."""
    u = make_floor_updater()
    arr = np.full((40, 40), 0.05, dtype=np.float32)  # < floor everywhere, no core at all

    result = u._apply_meaningful_floor(arr)

    assert (result == 0.0).all()


def test_apply_meaningful_floor_keeps_a_smooth_halo_around_a_real_core():
    """A real core (>= floor) keeps its immediate falloff smooth (nonzero, scaled
    down but not hard-clipped) within the blur's own support radius, while noise well
    outside that halo -- even at an identical raw magnitude -- is zeroed."""
    u = make_floor_updater(sigma_cells=1.2)  # radius = round(3*1.2) = 4
    arr = np.zeros((40, 40), dtype=np.float32)
    arr[20, 20] = 5.0  # a real core, well above floor
    arr[20, 22] = 0.05  # 2 cells away -- inside the halo (radius 4)
    arr[20, 35] = 0.05  # far away -- outside the halo, identical magnitude

    result = u._apply_meaningful_floor(arr)

    assert result[20, 20] == 5.0  # core untouched (t=1 at/above floor)
    assert result[20, 22] > 0.0  # inside the halo: smoothed, not zeroed
    assert result[20, 35] == 0.0  # outside the halo: zeroed despite equal magnitude


def test_apply_meaningful_floor_returns_all_zero_when_no_core_exists():
    """No core anywhere (e.g. a completely dry field) -- must not error, and must
    zero the whole field rather than leaving stray sub-floor noise."""
    u = make_floor_updater()
    arr = np.zeros((10, 10), dtype=np.float32)

    result = u._apply_meaningful_floor(arr)

    assert (result == 0.0).all()
