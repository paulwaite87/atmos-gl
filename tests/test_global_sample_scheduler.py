from atmos_gl.lib.flight_radar import GlobalSampleScheduler


def _make_scheduler(**overrides):
    kwargs = dict(
        fine_grid_deg=5.0,
        coarse_grid_deg=180.0,  # 2 coarse cells only -- small, deterministic test grid
        hot_cadence_s=10.0,
        background_cadence_s=10.0,
        starvation_floor_s=1800.0,
    )
    kwargs.update(overrides)
    return GlobalSampleScheduler(**kwargs)


def _prime_coarse_cells(sched, *, now):
    """Marks every coarse cell as freshly sampled, isolating hot-cell behaviour from
    the (also-never-sampled) background grid in tests that don't care about it."""
    for cell in sched._all_coarse_cells():
        sched.record_result(cell, [{"hex": "prime"}], now=now)


def test_all_coarse_cells_tile_the_globe_without_gaps():
    sched = _make_scheduler(coarse_grid_deg=30.0)
    assert len(sched._all_coarse_cells()) == 12 * 6  # 360/30 x 180/30


def test_set_interest_computes_the_fine_cell_at_the_viewport_center():
    sched = _make_scheduler()
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])  # center (0.5, 0.5)
    assert sched._hot_cells == {(5.0, 0, 0)}


def test_set_interest_recenters_every_call():
    sched = _make_scheduler()
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    sched.set_interest([(100.0, 0.0, 101.0, 1.0)])  # center (100.5, 0.5)
    assert sched._hot_cells == {(5.0, 20, 0)}


def test_set_interest_covers_the_whole_viewport_not_just_its_center_cell():
    """A viewport spanning multiple fine cells must mark ALL of them hot -- the whole
    visible area is "the hotspot", not just its center point (issue #215's original
    design intent; a bug in the first implementation only marked the center cell)."""
    sched = _make_scheduler()
    sched.set_interest([(0.0, 0.0, 12.0, 1.0)])  # spans lon cells 0, 1, 2 at 5deg
    assert sched._hot_cells == {(5.0, 0, 0), (5.0, 1, 0), (5.0, 2, 0)}


def test_set_interest_caps_hot_cells_per_viewport_nearest_center_first():
    sched = _make_scheduler()
    # A very wide, zoomed-out viewport touches far more than MAX_HOT_CELLS_PER_VIEWPORT
    # fine cells -- must degrade gracefully to the ones nearest the center instead of
    # claiming the whole visible area as hot (which would blow the request budget).
    sched.set_interest([(-50.0, -50.0, 50.0, 50.0)])
    assert len(sched._hot_cells) == 12  # MAX_HOT_CELLS_PER_VIEWPORT
    assert (5.0, 0, 0) in sched._hot_cells  # the center cell is always kept


def test_set_interest_unions_hot_cells_across_multiple_viewports():
    sched = _make_scheduler()
    sched.set_interest([(0.0, 0.0, 1.0, 1.0), (100.0, 0.0, 101.0, 1.0)])
    assert sched._hot_cells == {(5.0, 0, 0), (5.0, 20, 0)}


# ---- hotspot_progress: "N of M currently-prioritized cells have data" -----------

def test_hotspot_progress_is_zero_zero_with_no_active_viewport():
    sched = _make_scheduler()
    assert sched.hotspot_progress([]) == {"queried": 0, "total": 0}


def test_hotspot_progress_counts_an_unsampled_cell_as_not_queried():
    sched = _make_scheduler()
    viewports = [(0.0, 0.0, 1.0, 1.0)]
    assert sched.hotspot_progress(viewports) == {"queried": 0, "total": 1}


def test_hotspot_progress_reflects_a_sampled_cell():
    sched = _make_scheduler()
    viewports = [(0.0, 0.0, 1.0, 1.0)]
    sched.record_result((5.0, 0, 0), [{"hex": "a1"}], now=0.0)
    assert sched.hotspot_progress(viewports) == {"queried": 1, "total": 1}


def test_hotspot_progress_counts_partial_coverage_across_a_multi_cell_viewport():
    sched = _make_scheduler()
    viewports = [(0.0, 0.0, 12.0, 1.0)]  # 3 cells: (0,0), (1,0), (2,0)
    sched.record_result((5.0, 0, 0), [{"hex": "a1"}], now=0.0)
    assert sched.hotspot_progress(viewports) == {"queried": 1, "total": 3}


def test_hotspot_progress_counts_a_failed_fetch_as_queried():
    """A rejected/failed request still ATTEMPTED the cell -- record_result(None) still
    advances last_sampled_at, so it correctly counts toward "queried" here even though
    it didn't confirm any aircraft."""
    sched = _make_scheduler()
    viewports = [(0.0, 0.0, 1.0, 1.0)]
    sched.record_result((5.0, 0, 0), None, now=0.0)
    assert sched.hotspot_progress(viewports) == {"queried": 1, "total": 1}


def test_hotspot_progress_does_not_double_count_overlapping_viewports():
    sched = _make_scheduler()
    viewports = [(0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0)]  # identical, fully overlapping
    assert sched.hotspot_progress(viewports) == {"queried": 0, "total": 1}


# ---- global_coverage: "how much of the WHOLE globe has up-to-date data" --------
# (the Data Status Collectors panel's flightradar_collector percent -- see
# AircraftCollector.data_status()) -- deliberately independent of any viewport/
# hot cell, over the fixed coarse-grid tiling only.

def test_global_coverage_is_zero_of_total_before_anything_is_sampled():
    sched = _make_scheduler()  # coarse_grid_deg=180.0 -> 2 coarse cells
    assert sched.global_coverage(now=0.0) == {"fresh": 0, "total": 2}


def test_global_coverage_counts_a_cell_sampled_within_the_starvation_floor_as_fresh():
    sched = _make_scheduler(starvation_floor_s=1800.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "a1"}], now=0.0)
    assert sched.global_coverage(now=900.0) == {"fresh": 1, "total": 2}


def test_global_coverage_stops_counting_a_cell_once_past_the_starvation_floor():
    sched = _make_scheduler(starvation_floor_s=1800.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "a1"}], now=0.0)
    sched.record_result(c2, [{"hex": "a2"}], now=0.0)
    # c1 gets refreshed again well inside the floor; c2 is left to go stale.
    sched.record_result(c1, [{"hex": "a1"}], now=1700.0)
    assert sched.global_coverage(now=1800.1) == {"fresh": 1, "total": 2}


def test_global_coverage_counts_a_failed_fetch_as_fresh_same_as_hotspot_progress():
    """record_result(None) (a failed/rejected request) still advances last_sampled_at
    -- the cell was attempted this recently, even though nothing was confirmed."""
    sched = _make_scheduler()
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, None, now=0.0)
    assert sched.global_coverage(now=1.0) == {"fresh": 1, "total": 2}


def test_global_coverage_is_independent_of_hot_cells():
    """Hot cells (fine grid) never appear in _all_coarse_cells() -- an active,
    fully-covered viewport must not inflate global_coverage's total or fresh count."""
    sched = _make_scheduler()
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    sched.record_result((5.0, 0, 0), [{"hex": "a1"}], now=0.0)  # a hot cell, not coarse
    assert sched.global_coverage(now=0.0) == {"fresh": 0, "total": 2}


def test_next_cell_returns_the_hot_cell_once_it_is_the_only_due_cell():
    sched = _make_scheduler()
    _prime_coarse_cells(sched, now=0.0)
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    assert sched.next_cell(now=0.0) == (5.0, 0, 0)


def test_next_cell_prefers_the_longest_waiting_previously_sampled_background_cell():
    sched = _make_scheduler(background_cadence_s=2.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "a1"}], now=5.0)  # attempted more recently
    sched.record_result(c2, [{"hex": "a2"}], now=1.0)  # attempted longer ago

    assert sched.next_cell(now=10.0) == c2


def test_hot_cells_are_never_deprioritized_by_empty_streak():
    sched = _make_scheduler(background_cadence_s=100.0)
    _prime_coarse_cells(sched, now=0.0)
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    hot = (5.0, 0, 0)
    for t in (0.0, 1.0, 2.0, 3.0):
        sched.record_result(hot, [], now=t)

    # hot_cadence_s stays 10s despite 4 consecutive empty results; 10s after the last
    # sample it must be due again -- never stretched the way a background cell would be.
    assert sched.next_cell(now=13.0) == hot


def test_repeated_empty_results_stretch_a_background_cells_effective_cadence():
    sched = _make_scheduler(background_cadence_s=10.0, starvation_floor_s=1000.0)
    c1, c2 = sched._all_coarse_cells()

    for t in (0.0, 1.0, 2.0):
        sched.record_result(c1, [], now=t)  # 3 consecutive empties -> streak penalty
    sched.record_result(c2, [{"hex": "a1"}], now=2.0)

    # Both cells are 15s past their last sample. c2's un-stretched 10s cadence is due;
    # c1's streak-of-3 empties have stretched its effective cadence past 15s, so it
    # must NOT win.
    assert sched.next_cell(now=17.0) == c2


def test_record_result_of_none_does_not_advance_the_empty_streak():
    sched = _make_scheduler()
    c1, _ = sched._all_coarse_cells()
    sched.record_result(c1, [], now=0.0)
    sched.record_result(c1, [], now=1.0)
    sched.record_result(c1, None, now=2.0)  # failed fetch, not evidence of emptiness
    assert sched._empty_streak[c1] == 2


def test_starvation_floor_overrides_a_heavily_stretched_background_cell():
    sched = _make_scheduler(background_cadence_s=10.0, starvation_floor_s=50.0)
    c1, c2 = sched._all_coarse_cells()

    t = 0.0
    for _ in range(12):
        sched.record_result(c1, [], now=t)  # streak of 12 -> effective cadence capped at 10x = 100s
        t += 1.0
    sched.record_result(c2, [{"hex": "a1"}], now=40.0)

    # c1 has waited 51s (>= the 50s floor, though < its own stretched 100s cadence) and
    # must win outright; c2 has only waited 22s (>= its own un-stretched 10s cadence,
    # so it's "due" too, but nowhere near the floor).
    assert sched.next_cell(now=62.0) == c1


def test_next_cell_prioritizes_a_due_hot_cell_over_a_more_overdue_background_cell():
    """The actual reported bug: once background cells are ALSO due, oldest-timestamp
    -first alone lets a long-overdue background cell win over an active viewer's own
    hot cell, degrading the promised hot_cadence_s into a round-robin across the whole
    (hot + background) pool -- at default budget/grid sizes that's ~7 minutes per hot
    cell instead of the intended ~10-60s. A due hot cell must always win over a due
    background cell, regardless of which has waited longer."""
    sched = _make_scheduler(background_cadence_s=5.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "bg"}], now=0.0)  # far more overdue than the hot cell
    sched.record_result(c2, [{"hex": "bg"}], now=8.0)
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    hot = (5.0, 0, 0)
    sched.record_result(hot, [{"hex": "h1"}], now=9.0)  # sampled more recently than either

    # now=20: c1 waited 20s (>>5s cadence, most overdue of all); hot waited 11s
    # (>=10s cadence, also due). Oldest-first alone would return c1.
    assert sched.next_cell(now=20.0) == hot


def test_next_cell_suspends_background_entirely_while_any_viewport_is_active():
    """Background cache-warming is suspended completely whenever at least one
    viewport is active (hot_cells non-empty) -- FlightRadar viewers get 100% of the
    request budget for as long as they're watching, not a share split with the rest
    of the globe. A gap between a hot cell's own samples is NOT an invitation to spend
    budget elsewhere -- the tick simply goes idle until the hot cell is due again."""
    sched = _make_scheduler(background_cadence_s=5.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "bg"}], now=10.0)
    sched.record_result(c2, [{"hex": "bg"}], now=0.0)
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    hot = (5.0, 0, 0)
    sched.record_result(hot, [{"hex": "h1"}], now=15.0)  # just sampled, not due yet

    # now=16: hot waited 1s (< 10s cadence, not due); c2 waited 16s and would win
    # under the old "fill background gaps" behaviour -- but must not be touched at
    # all while a viewport is active.
    assert sched.next_cell(now=16.0) is None


def test_next_cell_resumes_background_cache_warming_once_no_viewport_is_active():
    """Once nobody is watching FlightRadar (no active interest -> no hot cells), the
    coarse background sweep resumes normally -- this is what keeps the globe warm for
    the next time someone opens the layer."""
    sched = _make_scheduler(background_cadence_s=5.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "bg"}], now=10.0)
    sched.record_result(c2, [{"hex": "bg"}], now=0.0)
    # no set_interest() call -- no active viewport

    assert sched.next_cell(now=16.0) == c2


def test_next_cell_ignores_a_floored_background_cell_while_a_viewport_is_active():
    """Caught live: suspending background sampling while a viewport is active is
    exactly what drives background cells past the starvation floor (they go
    completely untouched for as long as the viewport stays open) -- an unscoped floor
    check would then perpetually re-admit those floored cells and starve the hot cell
    all over again once a viewing session ran past starvation_floor_s, silently
    undoing viewport suspension after ~30 minutes of continuous viewing."""
    sched = _make_scheduler(starvation_floor_s=100.0)
    c1, c2 = sched._all_coarse_cells()
    sched.record_result(c1, [{"hex": "bg"}], now=0.0)
    sched.record_result(c2, [{"hex": "bg"}], now=0.0)
    sched.set_interest([(0.0, 0.0, 1.0, 1.0)])
    hot = (5.0, 0, 0)
    sched.record_result(hot, [{"hex": "h1"}], now=90.0)  # sampled well after the background cells

    # now=101: both background cells breached the 100s floor (101s overdue) -- under
    # the old unscoped check they'd win outright. The active viewport's hot cell
    # (only 11s overdue, due at its own 10s cadence) must win instead.
    assert sched.next_cell(now=101.0) == hot


def test_next_cell_is_none_when_nothing_is_due():
    sched = _make_scheduler(background_cadence_s=100.0, starvation_floor_s=1000.0)
    _prime_coarse_cells(sched, now=0.0)
    assert sched.next_cell(now=1.0) is None
