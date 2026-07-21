#!/usr/bin/env python3
"""Tests for layer_builder.py's round-robin per-hour dispatch (architecture review
candidate "interleave per-hour rendering across layers"): a multi-hour section used to
occupy a render-pool worker for its ENTIRE backlog (render_all_hours draining every
stale hour in one call) before the next queued section could start. _render_worker now
threads an optional max_hours through to run(), and start_scheduler() dispatches
multi-hour sections in rounds of one hour each instead of one all-hours call, so no
single section's backlog can monopolise the pool.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atmos_gl.layer_builder import (
    MULTI_HOUR_SECTIONS,
    SINGLE_SHOT_SECTIONS,
    TASK_CLASSES,
    _render_worker,
    _updater_class,
    LayerBuilder,
)


def test_multi_hour_and_single_shot_sections_partition_task_classes():
    assert set(MULTI_HOUR_SECTIONS) | set(SINGLE_SHOT_SECTIONS) == set(TASK_CLASSES)
    assert set(MULTI_HOUR_SECTIONS).isdisjoint(SINGLE_SHOT_SECTIONS)
    assert set(SINGLE_SHOT_SECTIONS) == {"sst", "clouds", "markers"}
    assert set(MULTI_HOUR_SECTIONS) == {
        "isobars", "precipitation", "wind", "currents", "waves",
        "temperature", "ozone", "stormwatch", "pwat", "fires",
    }


def test_updater_class_unwraps_partial_bindings():
    from atmos_gl.tasks.scalar_field import ScalarFieldUpdater

    assert _updater_class(TASK_CLASSES["ozone"]) is ScalarFieldUpdater
    assert _updater_class(TASK_CLASSES["isobars"]) is TASK_CLASSES["isobars"]


def test_every_multi_hour_section_run_accepts_max_hours():
    """_render_worker calls run(max_hours=max_hours) unconditionally for every
    TASK_CLASSES entry (see test_render_worker_forwards_max_hours_and_returns_plotted_count
    above) -- but that test only exercises a fake updater, so it can't catch a REAL
    multi-hour section whose run() doesn't declare max_hours. That gap let
    PrecipitationUpdater.run() ship without the parameter: every dispatch round raised
    TypeError("PrecipitationUpdater.run() got an unexpected keyword argument
    'max_hours'"), so precipitation silently never rendered a new hour while every other
    multi-hour layer kept advancing."""
    for section in MULTI_HOUR_SECTIONS:
        cls = _updater_class(TASK_CLASSES[section])
        params = inspect.signature(cls.run).parameters
        accepts_max_hours = "max_hours" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        assert accepts_max_hours, (
            f"{cls.__name__}.run() (section {section!r}) doesn't accept max_hours; "
            f"_render_worker calls every section's run(max_hours=...) unconditionally"
        )


def test_render_worker_forwards_max_hours_and_returns_plotted_count():
    fake_updater = MagicMock()
    fake_updater.run.return_value = 1
    fake_cls = MagicMock(return_value=fake_updater)

    with patch.dict("atmos_gl.layer_builder.TASK_CLASSES", {"fake": fake_cls}), \
         patch("atmos_gl.layer_builder.AtmosGLConfig"), \
         patch("atmos_gl.layer_builder.MapData"):
        result = _render_worker("cfg.json", "fake", {}, max_hours=1)

    fake_updater.run.assert_called_once_with(max_hours=1)
    assert result == ("fake", None, 1)


def test_render_worker_reports_zero_plotted_on_none_return():
    """Single-shot layers' run() returns None -- _render_worker must not propagate
    that as the plotted count (the round-robin loop treats None like 0, but coercing
    it here keeps the (section, error, plotted) tuple shape uniform)."""
    fake_updater = MagicMock()
    fake_updater.run.return_value = None
    fake_cls = MagicMock(return_value=fake_updater)

    with patch.dict("atmos_gl.layer_builder.TASK_CLASSES", {"fake": fake_cls}), \
         patch("atmos_gl.layer_builder.AtmosGLConfig"), \
         patch("atmos_gl.layer_builder.MapData"):
        result = _render_worker("cfg.json", "fake", {})

    assert result == ("fake", None, 0)


def test_render_worker_catches_exceptions():
    fake_cls = MagicMock(side_effect=RuntimeError("boom"))

    with patch.dict("atmos_gl.layer_builder.TASK_CLASSES", {"fake": fake_cls}), \
         patch("atmos_gl.layer_builder.AtmosGLConfig"), \
         patch("atmos_gl.layer_builder.MapData"):
        section, error, plotted = _render_worker("cfg.json", "fake", {})

    assert section == "fake"
    assert "boom" in error
    assert plotted == 0


def make_bare_layer_builder():
    lb = LayerBuilder.__new__(LayerBuilder)
    lb.process_status_adapter = MagicMock()
    return lb


def test_handle_results_reports_plotted_count_per_section():
    lb = make_bare_layer_builder()
    results = [("isobars", None, 3), ("pwat", None, 0), ("sst", None, 0)]

    broken, plotted_by_section = lb._handle_results(["isobars", "pwat", "sst"], results)

    assert broken is False
    assert plotted_by_section == {"isobars": 3, "pwat": 0, "sst": 0}


def test_handle_results_excludes_failed_sections_from_plotted():
    lb = make_bare_layer_builder()
    results = [("isobars", "RuntimeError('boom')", 0), ("pwat", None, 2)]

    broken, plotted_by_section = lb._handle_results(["isobars", "pwat"], results)

    assert broken is False
    assert "isobars" not in plotted_by_section
    assert plotted_by_section == {"pwat": 2}


def test_handle_results_still_detects_a_broken_pool():
    from concurrent.futures.process import BrokenProcessPool

    lb = make_bare_layer_builder()
    results = [BrokenProcessPool("worker died")]

    broken, plotted_by_section = lb._handle_results(["isobars"], results)

    assert broken is True
    assert plotted_by_section == {}


@pytest.mark.asyncio
async def test_run_dispatch_cycle_drops_sections_once_they_stop_reporting_progress():
    """Round 1 dispatches every section (single-shot + all multi-hour). Round 2 only
    re-dispatches multi-hour sections that still had a backlog last round -- single-shot
    sections never get a second round, and a multi-hour section with nothing left to
    render drops out just as quickly as one that never had anything pending."""
    lb = make_bare_layer_builder()
    round_1 = {s: 1 for s in ("isobars", "precipitation", "wind", "currents", "waves",
                               "temperature", "ozone", "stormwatch")}
    round_1["pwat"] = 0  # never had a backlog
    round_2 = {s: 0 for s in round_1 if round_1[s] > 0}  # everyone catches up in round 2
    lb._dispatch_round = AsyncMock(side_effect=[round_1, round_2])

    await lb._run_dispatch_cycle(loop=MagicMock(), baseline={})

    assert lb._dispatch_round.call_count == 2
    round_1_sections = set(lb._dispatch_round.call_args_list[0].args[1])
    round_2_sections = set(lb._dispatch_round.call_args_list[1].args[1])
    assert round_1_sections == set(SINGLE_SHOT_SECTIONS) | set(MULTI_HOUR_SECTIONS)
    assert round_2_sections == set(round_1) - {"pwat"}  # dropped: no backlog, single-shot


@pytest.mark.asyncio
async def test_run_dispatch_cycle_stops_once_nothing_reports_progress():
    lb = make_bare_layer_builder()
    lb._dispatch_round = AsyncMock(return_value={s: 0 for s in MULTI_HOUR_SECTIONS})

    await lb._run_dispatch_cycle(loop=MagicMock(), baseline={})

    lb._dispatch_round.assert_called_once()  # round 1 only -- nothing had a backlog
