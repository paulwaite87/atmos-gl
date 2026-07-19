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
    NZ_BBOX,
    TASMANIA_BBOX,
    BOUNDARY_SMOOTHING_DEG,
    _smooth_global_field,
    _isolate_antarctic_boundary,
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
    u._land_mask_cache = {}
    # plot() calls self._land_mask_for -- stub it out so tests don't hit real
    # coastline geometry; None makes plot() fall back to an all-sea-level mask.
    u._land_mask_for = MagicMock(return_value=None)
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


def _boundary_lat(result, lats, col):
    """Test helper: the interpolated zero-crossing latitude _isolate_antarctic_boundary's
    result actually renders for one column -- negative (south) values are cold/kept,
    non-negative (north) are warm/suppressed, by construction (see that function's
    docstring: every cell is a signed distance from the found boundary curve)."""
    order = np.argsort(lats)
    ordered_lats = lats[order]
    col_vals = result[order, col]
    is_warm = col_vals >= 0.0
    if not is_warm.any():
        return ordered_lats[-1]
    first_warm = int(np.argmax(is_warm))
    if first_warm == 0:
        return ordered_lats[0]
    lat0, lat1 = ordered_lats[first_warm - 1], ordered_lats[first_warm]
    v0, v1 = col_vals[first_warm - 1], col_vals[first_warm]
    frac = (0.0 - v0) / (v1 - v0)
    return lat0 + frac * (lat1 - lat0)


def _crossing_lats_by_column(cleaned, lats):
    """Test helper: the traced boundary location for every column."""
    return np.array(
        [_boundary_lat(cleaned, lats, col) for col in range(cleaned.shape[1])]
    )


def test_smooth_global_field_sigma_stabilizes_a_shallow_gradient_against_noise():
    """Regression test for a real live-data failure mode: wherever the temperature-
    vs-latitude profile is nearly flat (hovering within a fraction of a degree of
    freezing across many rows -- common in the open Southern Ocean), small per-cell
    noise can flip which row _isolate_antarctic_boundary's sweep finds as the first 0
    degC crossing by several degrees of latitude between one column and the next, even
    though nothing resembling a front boundary actually moved -- confirmed against
    live GFS data, where the old sigma=1.5 produced crossing jumps up to 5 degrees
    between adjacent 0.25 deg columns (sharp spikes and closed loops in the rendered
    line). SMOOTHING_SIGMA must be large enough to absorb that noise before the sweep
    runs; a too-small sigma is exactly the bug this guards against."""
    rng = np.random.default_rng(0)
    # Realistic GFS resolution (0.25 deg), matching what actually produced the live
    # 5-degree jumps -- a coarser synthetic grid smooths away the same noise trivially
    # even at the old sigma, since sigma is in GRID-CELL units and a coarse grid's
    # cells already span many real degrees each.
    lats = np.arange(-70.0, -40.0, 0.25)
    lons = np.arange(-140.0, -80.0, 0.25)
    n_lats, n_lons = len(lats), len(lons)
    no_land = np.zeros((n_lats, n_lons), dtype=bool)

    # A shallow south-to-north gradient crossing freezing around the middle of the
    # window, plus small per-cell noise -- the shape that made live columns 0.25 deg
    # apart disagree by several degrees of latitude.
    gradient = np.linspace(-2.0, 2.0, n_lats)[:, None]
    noisy = np.broadcast_to(gradient, (n_lats, n_lons)) + rng.normal(
        scale=0.3, size=(n_lats, n_lons)
    )

    with patch("atmos_gl.tasks.polar_boundary.SMOOTHING_SIGMA", 1.5):
        unstable = _isolate_antarctic_boundary(
            _smooth_global_field(noisy), lats, lons, no_land, smoothing_deg=0.0
        )
    with patch("atmos_gl.tasks.polar_boundary.SMOOTHING_SIGMA", 10.0):
        stable = _isolate_antarctic_boundary(
            _smooth_global_field(noisy), lats, lons, no_land, smoothing_deg=0.0
        )

    unstable_jumps = np.abs(np.diff(_crossing_lats_by_column(unstable, lats)))
    stable_jumps = np.abs(np.diff(_crossing_lats_by_column(stable, lats)))

    # The old, too-small sigma lets noise flip the crossing by several rows between
    # neighbouring columns; the production sigma keeps neighbours in close agreement.
    assert unstable_jumps.max() > stable_jumps.max()
    assert stable_jumps.max() <= 3.0 * abs(lats[1] - lats[0])


# --- _isolate_antarctic_boundary (a single Antarctic contour: sweep from the South
# Pole northward, keep only up to the FIRST 0 degC crossing, sea-level only except
# over NZ/Tasmania) ---

# North pole at row 0, south pole at row 4 -- matches GFS's north-first convention.
_LATS = np.array([90.0, 45.0, 0.0, -45.0, -90.0])
_LONS = np.array([90.0, 170.0])  # col 0: nowhere near NZ; col 1: inside NZ_BBOX's lon span
_NO_LAND = np.zeros((5, 2), dtype=bool)


def test_isolate_antarctic_boundary_keeps_cold_up_to_the_first_crossing():
    temp = np.full((5, 2), 10.0)
    temp[4, 0] = -5.0  # lat -90 (South Pole): cold
    temp[3, 0] = -3.0  # lat -45: cold, connected to the pole
    temp[2, 0] = 5.0   # lat 0: warms up -- the boundary
    # lat 45 / 90 (rows 0-1) stay at the default 10.0

    result = _isolate_antarctic_boundary(
        temp.copy(), _LATS, _LONS, _NO_LAND, smoothing_deg=0.0
    )

    # South of the real data's crossing: negative (cold/kept), by construction --
    # every cell is now a signed distance from the found boundary curve, not the
    # original temperature (see _isolate_antarctic_boundary's docstring for why: a
    # discrete "preserve the real value south of the crossing" cutoff is exactly what
    # produced the staircase this rewrite fixes).
    assert result[4, 0] < 0.0
    assert result[3, 0] < 0.0
    # The interpolated crossing lands strictly between the last cold (-45) and first
    # warm (0) real readings -- not snapped to either grid row.
    boundary = _boundary_lat(result, _LATS, 0)
    assert -45.0 < boundary < 0.0


def test_isolate_antarctic_boundary_ignores_a_second_cold_dip_further_north():
    """Only the FIRST 0 degC crossing counts -- a second cold pocket north of it
    (e.g. a mountain range with nothing to do with the polar front) must not draw a
    second line."""
    temp = np.full((5, 2), 10.0)
    temp[4, 0] = -5.0  # lat -90: cold
    temp[3, 0] = 1.0   # lat -45: the first crossing
    temp[2, 0] = -8.0  # lat 0: cold again -- must be suppressed, not a second boundary

    result = _isolate_antarctic_boundary(
        temp.copy(), _LATS, _LONS, _NO_LAND, smoothing_deg=0.0
    )

    # Everything from the first crossing northward reads non-negative (suppressed) --
    # including the second cold dip at lat 0, which would otherwise reopen a second
    # boundary.
    assert result[2, 0] >= 0.0
    assert result[1, 0] >= 0.0
    assert result[0, 0] >= 0.0
    boundary = _boundary_lat(result, _LATS, 0)
    assert -90.0 < boundary < -45.0  # the one crossing found, between the pole and -45


def test_isolate_antarctic_boundary_never_draws_the_arctic():
    """Northern Hemisphere rows are always suppressed, even during a hypothetical
    Arctic cold snap -- this layer draws exactly one (Antarctic) boundary."""
    temp = np.full((5, 2), -20.0)  # cold everywhere, including both poles
    result = _isolate_antarctic_boundary(
        temp.copy(), _LATS, _LONS, _NO_LAND, smoothing_deg=0.0
    )
    assert np.all(result[_LATS > 0.0] == VMAX_TEMPERATURE)


def test_isolate_antarctic_boundary_reads_sea_level_through_land():
    """A land cell (e.g. high-altitude terrain, permanently cold regardless of the
    polar airmass) is excluded from the sweep -- it inherits the NEAREST true
    sea-level reading (2D, any direction: distance_transform_edt, not a directional
    forward-fill), not its own value. Ocean on both sides of the land cell is cold
    here so the test isn't sensitive to which of the two equidistant-ish neighbours
    the fill happens to pick -- that ambiguity isn't what this test is about (see the
    Chile-coast case in the class/function docstring for why direction matters at
    all: a plain south-to-north forward-fill would have dragged a value from
    thousands of km away up a whole coastline)."""
    lats = np.array([90.0, 70.0, 50.0, 30.0, 10.0, -10.0, -30.0, -50.0, -70.0, -90.0])
    lons = _LONS
    no_land = np.zeros((10, 2), dtype=bool)
    land = no_land.copy()
    land[7, 0] = True  # lat -50, col 0: LAND

    temp = np.full((10, 2), 10.0)
    temp[9, 0] = -5.0    # lat -90: ocean, cold
    temp[8, 0] = -5.0    # lat -70: ocean, cold
    temp[7, 0] = -100.0  # lat -50: LAND -- absurdly cold, must be ignored
    temp[6, 0] = -2.0    # lat -30: ocean, ALSO cold (both neighbours agree)
    temp[5, 0] = 5.0     # lat -10: ocean, warm -- the real crossing

    result = _isolate_antarctic_boundary(
        temp.copy(), lats, lons, land, smoothing_deg=0.0
    )

    # The land cell reads as cold (south of the crossing) via the sea-level fill --
    # its own absurd -100 never enters the calculation, and it isn't suppressed as if
    # it were past the crossing.
    assert result[7, 0] < 0.0
    # The crossing is found between the real ocean readings flanking it (-30, -10),
    # unaffected by the land cell sitting further south.
    boundary = _boundary_lat(result, lats, 0)
    assert -30.0 < boundary < -10.0


def test_isolate_antarctic_boundary_uses_actual_readings_over_nz_and_tasmania():
    """Same land cell, same values, but at a longitude inside NZ_BBOX -- here the
    land's own reading drives the sweep instead of being papered over by the
    sea-level fill, since a real front reaching NZ is the point of the layer."""
    assert TASMANIA_BBOX[0] < NZ_BBOX[0]  # sanity: they're distinct boxes
    land = _NO_LAND.copy()
    land[3, 1] = True  # lat -45, col 1 (lon 170 -- inside NZ_BBOX) is land

    temp = np.full((5, 2), 10.0)
    temp[4, 1] = -5.0  # lat -90: ocean, cold
    temp[3, 1] = -3.0  # lat -45: LAND, but inside NZ -- its own reading is used
    temp[2, 1] = 5.0   # lat 0: ocean, warm

    result = _isolate_antarctic_boundary(
        temp.copy(), _LATS, _LONS, land, smoothing_deg=0.0
    )

    boundary = _boundary_lat(result, _LATS, 1)
    # Exactly what interpolating between the land cell's OWN -3.0 reading and the
    # lat-0 ocean crossing gives. If the sea-level fill had overridden the land cell
    # with a flanking ocean reading instead (-5.0 or 5.0, per
    # test_isolate_antarctic_boundary_reads_sea_level_through_land's fill-tie
    # discussion), this would land at -22.5 or -67.5 instead -- nowhere near here.
    assert abs(boundary - (-28.125)) < 0.01


def test_isolate_antarctic_boundary_smooths_a_jagged_crossing_across_longitude():
    """Real land-mask edges make the raw per-column crossing step around abruptly --
    smoothing_deg pulls a single-column spike back toward its neighbours' crossing
    latitude, the same way _smooth_global_field pulls a single-cell temperature spike
    toward ITS neighbours, just applied to the boundary's location instead. Uses a
    fine latitude grid and a large, isolated spike so the shift is unambiguous rather
    than resting on a single knife-edge row."""
    n_lons = 61
    n_lats = 91
    lats = np.linspace(90.0, -90.0, n_lats)  # 2 deg steps, north pole first
    lons = np.linspace(-180.0, 180.0, n_lons, endpoint=False)
    no_land = np.zeros((n_lats, n_lons), dtype=bool)

    # Every column: cold south of -20, warm from -18 northward (crossing near -19).
    temp = np.full((n_lats, n_lons), 5.0)
    temp[lats <= -20.0, :] = -3.0

    # One column: warm all the way down to -88 -- an isolated spike crossing far
    # closer to the pole than its neighbours.
    spike_col = n_lons // 2
    temp[lats <= -20.0, spike_col] = 5.0
    temp[lats <= -88.0, spike_col] = -3.0

    unsmoothed = _isolate_antarctic_boundary(
        temp.copy(), lats, lons, no_land, smoothing_deg=0.0
    )
    smoothed = _isolate_antarctic_boundary(
        temp.copy(), lats, lons, no_land, smoothing_deg=BOUNDARY_SMOOTHING_DEG
    )

    unsmoothed_kept = np.sum(unsmoothed[:, spike_col] < 0.0)
    smoothed_kept = np.sum(smoothed[:, spike_col] < 0.0)

    # Unsmoothed, the spike column's own crossing sits right near the pole, so almost
    # nothing stays "kept". Smoothed, neighbours -- which don't warm up until much
    # further north -- pull that boundary out with them, so more rows stay "kept".
    assert smoothed_kept > unsmoothed_kept


def test_isolate_antarctic_boundary_absorbs_a_land_fill_plateau_edge():
    """Regression test for a real live-data failure mode: the 2D nearest-ocean land
    fill (see _isolate_antarctic_boundary's docstring) can make several adjacent
    inland columns share the identical nearest true-ocean cell, so the filled proxy
    value is a flat plateau with a HARD EDGE wherever that nearest cell changes. Two
    adjacent plateaus a few grid columns wide, differing by under 3 degC -- but
    straddling freezing, where the latitude gradient is shallow -- produced a live
    5+ degree crossing-latitude jump (Patagonia) with the OLD design (a single global
    spline smoothing factor cranked up to absorb it, BOUNDARY_SMOOTHING_DEG=3.0):
    that fixed this specific case but flattened genuine regional structure into a
    near-perfect circle everywhere else, since FITPACK's smoothing factor is a single
    global budget, not a local one. MEDIAN_FILTER_DEG's local median filter is what
    actually carries this fix now -- confirmed against live data across every hour of
    a full run (worst case dropped from ~5 degrees to ~2.9, most hours under 1
    degree) -- with the spline left light (0.6) purely for cosmetic continuity. This
    reproduces the plateau shape directly, at a realistic width (bypassing the
    land-fill machinery itself, which isn't what's under test)."""
    n_lats = 60
    lats = np.linspace(90.0, -90.0, n_lats)
    lon_step = 0.25
    lons = np.arange(-100.0, -60.0, lon_step)  # realistic (0.25 deg) resolution
    n_lons = len(lons)
    no_land = np.zeros((n_lats, n_lons), dtype=bool)

    # Cold at the pole, warm north of -20 everywhere -- except a shallow plateau
    # straddling freezing between -40 and -20, split into two halves by one hard
    # seam only ~1.5 deg (6 columns) wide either side -- matching the scale actually
    # observed live, not an unrealistically wide (tens of degrees) anomaly a median
    # filter has no business erasing.
    temp = np.full((n_lats, n_lons), 5.0)
    temp[lats <= -40.0, :] = -3.0
    plateau_rows = (lats > -40.0) & (lats <= -20.0)
    mid = n_lons // 2
    half_width = int(1.5 / lon_step)
    lo, hi = max(0, mid - half_width), min(n_lons, mid + half_width)
    temp[np.ix_(plateau_rows, np.arange(lo, mid))] = -0.3
    temp[np.ix_(plateau_rows, np.arange(mid, hi))] = 0.3

    unsmoothed = _isolate_antarctic_boundary(
        temp.copy(), lats, lons, no_land, smoothing_deg=0.0
    )
    production = _isolate_antarctic_boundary(
        temp.copy(), lats, lons, no_land, smoothing_deg=BOUNDARY_SMOOTHING_DEG
    )

    def max_jump(result):
        b = _crossing_lats_by_column(result, lats)
        return np.abs(np.diff(b)).max()

    assert max_jump(unsmoothed) > 10.0  # the raw seam really is this sharp
    assert max_jump(production) < 1.0   # production settings absorb it


def test_layer_builder_registers_polar_boundary():
    from atmos_gl.layer_builder import TASK_CLASSES

    assert TASK_CLASSES["polar_boundary"] is PolarBoundaryUpdater


def test_output_files_registers_polar_boundary():
    from atmos_gl.lib.output_files import OUTFILES

    assert OUTFILES["polar_boundary"] == "data/polar_boundary.png"
