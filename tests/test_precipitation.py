#!/usr/bin/env python3
"""Tests for PrecipitationUpdater.save_precipitation_key -- verifies it matches the
key style shared by every other layer's key (sst/currents/waves/scalar_field), since
this key used to be smaller/unbolded with no explicit tick_format, which is why it
visibly looked different from those."""
from unittest.mock import MagicMock

from atmos_gl.tasks.precipitation import PrecipitationUpdater


def make_bare_updater(settings=None):
    """Bypass Updater.__init__ (does config/IO) and wire only what
    save_precipitation_key reads."""
    u = PrecipitationUpdater.__new__(PrecipitationUpdater)
    u.settings = settings or {}
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
    u = make_bare_updater(settings={"key_fontsize": 14})

    u.save_precipitation_key("/tmp/out/precipitation.png")

    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 14
