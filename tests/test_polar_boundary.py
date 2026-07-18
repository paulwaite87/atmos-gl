#!/usr/bin/env python3
"""Tests for PolarBoundaryUpdater -- the 0 degC isotherm contour line, rendered from
the SAME "temperature" fieldstore product the Air Temperature layer's filled heatmap
already consumes (see tasks/scalar_field.py's SPECS["temperature"]). Mirrors
IsobarUpdater's contour-line pattern, simplified to one fixed level (no isobar_step,
no per-line labels).
"""
from unittest.mock import patch, MagicMock

import numpy as np

from atmos_gl.tasks.common import ForecastState
from atmos_gl.tasks.polar_boundary import (
    PolarBoundaryUpdater,
    FREEZE_LEVEL_C,
    VMIN_TEMPERATURE,
    VMAX_TEMPERATURE,
    MAX_DEGREES_FROM_POLE,
    _smooth_global_field,
    _suppress_non_polar_cold_pockets,
)


def make_bare_updater(settings=None):
    """Bypass Updater.__init__ (does config/IO) and wire only what plot()/run() read
    -- same approach as test_scalar_field.py's make_bare_updater."""
    u = PolarBoundaryUpdater.__new__(PolarBoundaryUpdater)
    u.section = "polar_boundary"
    u.status_product = "temperature"
    u.settings = settings or {}
    u.map_data = MagicMock()
    u.map_data.region.region_identifier = "global"
    u.output_path = "/tmp/out/polar_boundary.png"
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/polar_boundary_f003.png")
    return u


def test_init_passes_the_exact_section_key_for_outfile_lookup():
    """Regression test: section must be the literal config-key "polar_boundary" (not a
    display-cased phrase like "Polar Boundary") -- Updater.__init__ lowercases it but
    does NOT replace spaces with underscores, so a display phrase here silently breaks
    self.outfile's OUTFILES lookup. Caught live: layer_status() crashed with "expected
    str, bytes or os.PathLike object, not NoneType" from a None output_path."""
    with patch(
        "atmos_gl.tasks.polar_boundary.Updater.__init__", return_value=None
    ) as mock_init:
        PolarBoundaryUpdater(config=MagicMock(), map_data=MagicMock())
    assert mock_init.call_args.args[1] == "polar_boundary"


def test_plot_contours_at_the_freezing_level_only():
    u = make_bare_updater(settings={"line_color": "cyan", "linewidth": 2.0, "opacity": 90})
    field0 = {"lat": [0], "lon": [0], "values": [[1.0]]}
    state = ForecastState.at_hour("2026-06-13", "18", 3)

    with patch("atmos_gl.tasks.polar_boundary.Plot") as MockPlot, patch(
        "atmos_gl.tasks.polar_boundary.encode_frames"
    ) as mock_encode:
        u.plot(field0, state)

    contour = MockPlot.return_value.ax.contour
    contour.assert_called_once()
    assert contour.call_args.kwargs["levels"] == [FREEZE_LEVEL_C]
    assert contour.call_args.kwargs["colors"] == "cyan"
    assert contour.call_args.kwargs["linewidths"] == 2.0
    assert contour.call_args.kwargs["alpha"] == 0.9

    # The data texture always uses temperature's own encode range (matching
    # temperature.js's VMIN/VMAX), not a range specific to this layer -- it's decoding
    # the SAME field the Air Temperature layer renders.
    assert mock_encode.call_args.args[2] == VMIN_TEMPERATURE
    assert mock_encode.call_args.args[3] == VMAX_TEMPERATURE


def test_plot_defaults_when_settings_absent():
    u = make_bare_updater(settings={})
    field0 = {"lat": [0], "lon": [0], "values": [[1.0]]}
    state = ForecastState.at_hour("2026-06-13", "18", 3)

    with patch("atmos_gl.tasks.polar_boundary.Plot") as MockPlot, patch(
        "atmos_gl.tasks.polar_boundary.encode_frames"
    ):
        u.plot(field0, state)

    contour = MockPlot.return_value.ax.contour
    assert contour.call_args.kwargs["colors"] == "cyan"
    assert contour.call_args.kwargs["linewidths"] == 2.0
    assert contour.call_args.kwargs["alpha"] == 0.9


def test_run_renders_from_the_temperature_product():
    u = make_bare_updater()
    u.get_gfs_state = MagicMock()
    u.render_all_hours = MagicMock(return_value=2)

    result = u.run(max_hours=1)

    u.get_gfs_state.assert_called_once()
    u.render_all_hours.assert_called_once()
    args, kwargs = u.render_all_hours.call_args
    assert args[0] == "temperature"
    assert kwargs["plot_fn"] == u.plot
    assert kwargs["max_hours"] == 1
    assert result == 2


def test_smooth_global_field_pulls_a_single_cell_spike_toward_its_neighbours():
    values = np.full((9, 9), 5.0)
    values[4, 4] = -50.0  # an isolated, wildly out-of-place cold spike
    smoothed = _smooth_global_field(values)
    assert smoothed.shape == values.shape
    assert smoothed[4, 4] > values[4, 4]


def test_smooth_global_field_wraps_at_the_antimeridian():
    """A spike right at the seam (column 0) should blur into BOTH the next column
    (col 1) and the wrapped-around last column, not just one side -- otherwise the
    left/right edges of the rendered globe would show a visible seam."""
    values = np.full((9, 9), 5.0)
    values[4, 0] = -50.0
    smoothed = _smooth_global_field(values)
    assert smoothed[4, 1] < 5.0        # blurred rightward, as expected either way
    assert smoothed[4, -1] < 5.0       # ALSO blurred into the wrapped-around column


# --- _suppress_non_polar_cold_pockets (only the Polar Boundary itself should draw a
# contour -- not an isolated cold pocket like a mountain range elsewhere on the globe)
# ---

# North pole at row 0, south pole at row 4 -- matches GFS's north-first convention.
_LATS = np.array([90.0, 45.0, 0.0, -45.0, -90.0])


def test_suppress_non_polar_cold_pockets_noop_when_nothing_is_cold():
    temp = np.full((5, 8), 10.0)
    result = _suppress_non_polar_cold_pockets(temp.copy(), _LATS)
    assert np.array_equal(result, temp)


def test_suppress_non_polar_cold_pockets_keeps_a_pole_connected_region():
    temp = np.full((5, 8), 10.0)
    temp[0, 2:5] = -3.0  # touches the north pole row directly
    result = _suppress_non_polar_cold_pockets(temp.copy(), _LATS)
    assert np.all(result[0, 2:5] < 0)


def test_suppress_non_polar_cold_pockets_removes_an_isolated_pocket():
    temp = np.full((5, 8), 10.0)
    temp[2, 3] = -5.0  # touches neither pole row -- e.g. a mountain range
    result = _suppress_non_polar_cold_pockets(temp.copy(), _LATS)
    assert result[2, 3] == VMAX_TEMPERATURE


def test_suppress_non_polar_cold_pockets_respects_antimeridian_wrap():
    """A cold path that only reaches the pole by crossing the antimeridian must still
    be recognised as pole-connected, not wrongly treated as two separate pockets.
    Uses its own lats (all close to the pole) rather than the shared _LATS -- this
    test is about wrap adjacency, not the MAX_DEGREES_FROM_POLE backstop below."""
    wrap_lats = np.array([90.0, 80.0, 70.0, -80.0, -90.0])
    temp = np.full((5, 8), 10.0)
    temp[0, 7] = -3.0  # touches the north pole row
    temp[1, 7] = -3.0  # connects straight down (ordinary adjacency)
    temp[1, 0] = -3.0  # same row as the above -- reachable only via antimeridian wrap
    temp[2, 0] = -3.0  # connects straight down from there

    result = _suppress_non_polar_cold_pockets(temp.copy(), wrap_lats)

    assert result[2, 0] < 0  # only correct if the wrap linked it back to the pole


# --- MAX_DEGREES_FROM_POLE backstop (the Andes case: a front that genuinely reaches
# Patagonia can become topologically connected to the permanently-cold high Andes,
# which connectivity alone can't distinguish from "the front got that far") ---


def test_suppress_non_polar_cold_pockets_backstop_clips_reach_beyond_max_degrees_from_pole():
    lats = np.array([90.0, 30.0, 10.0, -90.0])  # distances from nearest pole: 0, 60, 80, 0
    assert 60 <= MAX_DEGREES_FROM_POLE < 80  # keep the test meaningful if the constant moves
    temp = np.full((4, 3), 10.0)
    temp[0, 1] = -3.0  # touches the north pole row
    temp[1, 1] = -3.0  # 60 degrees from the pole -- within reach, part of the front
    temp[2, 1] = -3.0  # 80 degrees from the pole -- same connected chain, too far

    result = _suppress_non_polar_cold_pockets(temp.copy(), lats)

    assert result[1, 1] < 0
    assert result[2, 1] == VMAX_TEMPERATURE


def test_layer_builder_registers_polar_boundary():
    from atmos_gl.layer_builder import TASK_CLASSES

    assert TASK_CLASSES["polar_boundary"] is PolarBoundaryUpdater


def test_output_files_registers_polar_boundary():
    from atmos_gl.lib.output_files import OUTFILES

    assert OUTFILES["polar_boundary"] == "data/polar_boundary.png"
