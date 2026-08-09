#!/usr/bin/env python3
"""Tests for WindUpdater.plot(). The legend key is entirely client-side now (issue
#302, see ui/modules/wind.js's own PALETTE/buildLUT and wind_meta.json for the
data-dependent VMAX_SPEED scale) -- WindUpdater no longer renders one at all."""
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.common import ForecastState
from atmos_gl.tasks.wind import WindUpdater


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
