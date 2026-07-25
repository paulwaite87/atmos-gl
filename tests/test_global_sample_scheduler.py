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


def test_next_cell_is_none_when_nothing_is_due():
    sched = _make_scheduler(background_cadence_s=100.0, starvation_floor_s=1000.0)
    _prime_coarse_cells(sched, now=0.0)
    assert sched.next_cell(now=1.0) is None
