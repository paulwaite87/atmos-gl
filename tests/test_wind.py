#!/usr/bin/env python3
"""Tests for WindUpdater.save_wind_key -- wind previously had no backend key at all
(its legend was a hand-built client-side DOM gradient bar in ui/modules/wind.js, the
one layer visibly inconsistent with every other layer's key: different bar height/
length, its own font-size convention, no shared styling code). This locks the new
key's shape: VMAX_SPEED (m/s) expressed as km/h ticks, matching the shared key style
every other layer (sst/currents/waves/precipitation) now uses."""
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.common import ForecastState
from atmos_gl.tasks.wind import WindUpdater


def make_bare_updater(settings=None, vmax_speed_ms=100.0 / 3.6, common=None):
    """Bypass Updater.__init__ (does config/IO) and wire only what save_wind_key
    reads."""
    u = WindUpdater.__new__(WindUpdater)
    u.settings = settings or {}
    u.common = common or {}
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
    """key_fontsize is a shared common.key_fontsize setting, not this layer's own
    section (issue: consolidate the 11 per-layer key_fontsize settings)."""
    u = make_bare_updater(common={"key_fontsize": 14})

    u.save_wind_key("/tmp/out/wind.png")

    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 14


def make_bare_plot_updater():
    """Bypass Updater.__init__ (does config/IO) and wire only what plot() reads."""
    u = WindUpdater.__new__(WindUpdater)
    u.section = "wind"
    u.VMAX_SPEED = 100.0 / 3.6
    u.VMAX_WIND = 40.0
    u.map_data = MagicMock()
    u.map_data.region.region_identifier = "global"
    u.regrid_for_lod = MagicMock(return_value=([0], [0], [[0]]))
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/wind_f003.png")
    return u


def test_plot_still_encodes_velocity_texture_when_contourf_raises():
    """Regression for issue #283: WindUpdater.plot() coupled the contourf static
    heatmap and the velocity texture encode (encode_uv) in one unguarded call, so the
    same deterministic Cartopy antimeridian-wrapping-polygon bug fixed for
    ScalarFieldUpdater (PR #281) also blocked the velocity texture -- what the
    frontend's animated WebGL wind layer actually reads -- not just the legacy static
    heatmap, for any hour whose field topology triggers it."""
    u = make_bare_plot_updater()
    field0 = {
        "lat": np.array([0.0]),
        "lon": np.array([0.0]),
        "u": np.array([[1.0]]),
        "v": np.array([[1.0]]),
    }
    state = ForecastState.at_hour("2026-06-13", "18", 3)

    with patch("atmos_gl.tasks.wind.Plot") as MockPlot, patch(
        "atmos_gl.tasks.wind.encode_uv"
    ) as mock_encode:
        MockPlot.return_value.ax.contourf.side_effect = ValueError(
            "Sequences of multi-polygons are not valid arguments"
        )
        u.plot(field0, state)  # must not raise

    mock_encode.assert_called_once()
