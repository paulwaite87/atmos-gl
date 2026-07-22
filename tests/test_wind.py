#!/usr/bin/env python3
"""Tests for WindUpdater.save_wind_key -- wind previously had no backend key at all
(its legend was a hand-built client-side DOM gradient bar in ui/modules/wind.js, the
one layer visibly inconsistent with every other layer's key: different bar height/
length, its own font-size convention, no shared styling code). This locks the new
key's shape: VMAX_SPEED (m/s) expressed as km/h ticks, matching the shared key style
every other layer (sst/currents/waves/precipitation) now uses."""
from unittest.mock import MagicMock

from atmos_gl.tasks.wind import WindUpdater


def make_bare_updater(settings=None, vmax_speed_ms=100.0 / 3.6):
    """Bypass Updater.__init__ (does config/IO) and wire only what save_wind_key
    reads."""
    u = WindUpdater.__new__(WindUpdater)
    u.settings = settings or {}
    u.VMAX_SPEED = vmax_speed_ms
    u.save_key_image = MagicMock()
    return u


def test_save_wind_key_expresses_vmax_speed_as_kph_ticks():
    u = make_bare_updater(vmax_speed_ms=60.0 / 3.6)  # 60 km/h

    u.save_wind_key("/tmp/out/wind.png")

    u.save_key_image.assert_called_once()
    key_args = u.save_key_image.call_args
    assert key_args.args[0] == "/tmp/out/wind.png"
    assert list(key_args.args[3]) == [0.0, 15.0, 30.0, 45.0, 60.0]
    assert key_args.args[4] == "Wind speed (km/h)"


def test_save_wind_key_matches_the_shared_key_style():
    u = make_bare_updater()

    u.save_wind_key("/tmp/out/wind.png")

    key_args = u.save_key_image.call_args
    assert key_args.kwargs["key_fontsize"] == 10
    assert key_args.kwargs["labelsize"] == 8
    assert key_args.kwargs["weight"] == "bold"
    assert key_args.kwargs["tick_format"] == "%d"


def test_save_wind_key_honours_a_configured_key_fontsize():
    u = make_bare_updater(settings={"key_fontsize": 14})

    u.save_wind_key("/tmp/out/wind.png")

    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 14
