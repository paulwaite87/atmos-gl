#!/usr/bin/env python3
"""Tests for JetStreamUpdater (#182). Mirrors test_wind.py/test_currents.py's
bare-updater pattern (bypass Updater.__init__, wire only what the method under test
reads). The palette + legend key are entirely client-side now (issue #302, see
ui/modules/jetstream.js's own PALETTES/buildLUT) -- VectorFieldUpdater no longer
knows about palettes at all."""
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.jetstream import JetStreamUpdater


def make_bare_updater(settings=None, common=None):
    u = JetStreamUpdater.__new__(JetStreamUpdater)
    u.settings = settings or {}
    u.common = common or {}
    u.status_product = "jetstream"
    u.output_path = "/tmp/out/jetstream.png"
    return u


# ---- _warm_baseline_cache -------------------------------------------------------

def test_warm_baseline_cache_calls_get_gfs_state_not_rtofs():
    u = make_bare_updater()
    u.get_gfs_state = MagicMock()
    u.get_rtofs_state = MagicMock()
    u._warm_baseline_cache()
    u.get_gfs_state.assert_called_once()
    u.get_rtofs_state.assert_not_called()


# ---- plot -------------------------------------------------------------------

def test_plot_writes_the_velocity_texture_at_the_configured_vmax():
    u = make_bare_updater()
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/jetstream_f024.png")
    field0 = {
        "u": np.array([[10.0, 20.0]], dtype=np.float32),
        "v": np.array([[-5.0, 5.0]], dtype=np.float32),
        "lat": np.array([45.0]),
    }
    state = MagicMock(fhour=24)

    with patch("atmos_gl.tasks.jetstream.encode_uv") as mock_encode:
        u.plot(field0, state)

    mock_encode.assert_called_once()
    args, kwargs = mock_encode.call_args
    assert np.array_equal(args[0], field0["u"])
    assert np.array_equal(args[1], field0["v"])
    assert args[2] == "/tmp/out/jetstream_f024_data.png"
    assert args[3] == 120.0
    assert kwargs["lat"] is field0["lat"]


def test_plot_writes_no_static_heatmap_png():
    """Regression guard for the 'no heatmap' decision: plot() must produce exactly
    one output (the _data.png texture), never a plain .png."""
    u = make_bare_updater()
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/jetstream_f000.png")
    field0 = {
        "u": np.array([[1.0]], dtype=np.float32),
        "v": np.array([[1.0]], dtype=np.float32),
        "lat": np.array([0.0]),
    }
    state = MagicMock(fhour=0)

    with patch("atmos_gl.tasks.jetstream.encode_uv") as mock_encode:
        u.plot(field0, state)

    assert mock_encode.call_count == 1

