#!/usr/bin/env python3
"""Tests for JetStreamUpdater (#182). Mirrors test_wind.py/test_currents.py's
bare-updater pattern (bypass Updater.__init__, wire only what the method under test
reads)."""
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.jetstream import JetStreamUpdater


def make_bare_updater(settings=None):
    u = JetStreamUpdater.__new__(JetStreamUpdater)
    u.settings = settings or {}
    u.save_key_image = MagicMock()
    u.status_product = "jetstream"
    u.output_path = "/tmp/out/jetstream.png"
    return u


# ---- _palette -----------------------------------------------------------------

def test_palette_defaults_to_stratosphere_when_unset():
    u = make_bare_updater()
    assert u._palette() == "stratosphere"


def test_palette_falls_back_for_an_unknown_name():
    u = make_bare_updater(settings={"palette": "not-a-real-palette"})
    assert u._palette() == "stratosphere"


# ---- save_key (inherited from VectorFieldUpdater) -------------------------------

def test_save_key_uses_the_120_ms_vmax_range():
    u = make_bare_updater()
    u.save_key("/tmp/out/jetstream.png")
    key_args = u.save_key_image.call_args
    assert key_args.args[0] == "/tmp/out/jetstream.png"
    assert list(key_args.args[3]) == [0.0, 40.0, 80.0, 120.0]
    assert key_args.args[4] == "Jet Stream Speed (m/s)"


def test_save_key_uses_whole_number_ticks():
    u = make_bare_updater()
    u.save_key("/tmp/out/jetstream.png")
    assert u.save_key_image.call_args.kwargs["tick_format"] == "%.0f"


def test_save_key_honours_a_configured_key_fontsize():
    u = make_bare_updater(settings={"key_fontsize": 16})
    u.save_key("/tmp/out/jetstream.png")
    assert u.save_key_image.call_args.kwargs["key_fontsize"] == 16


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

