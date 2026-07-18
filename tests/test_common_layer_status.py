#!/usr/bin/env python3
"""Regression tests for Updater.layer_status() after routing it through
lib/data_status.py (architecture review candidate "one status module") -- locks both
branches' dict shape and percent calculation, since this had no test coverage before
this refactor.

The multi-hour branch filters catalog hours to "now onward" (see _now_fhour) -- these
tests build run_date/run_id relative to the real wall clock (same pattern
test_field_collector_base_data_status.py uses for FieldCollectorBase.data_status(),
which has the identical "now, computed from a run baseline" dependency) rather than a
hardcoded date, so a fixed catalog hour list stays meaningfully "now onward" or "past"
regardless of when the suite runs.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from atmos_gl.tasks.common import Updater


def make_bare_updater(section="isobars", settings=None, status_product=None):
    u = Updater.__new__(Updater)
    u.section = section
    u.settings = settings or {}
    u.enabled = u.settings.get("enabled", False)
    u.status_product = status_product
    u.process_status_adapter = MagicMock()
    return u


# _now_fhour resolves "now" to whichever forecast hour's valid time is CLOSEST to the
# wall clock (round(), not floor()) -- so the reference instant a baseline must sit on
# to land exactly on forecast hour 0 is "now" rounded to the nearest hour, not the
# current hour's floor (which can be up to 30 minutes off and round the wrong way).
def _reference_now():
    now = datetime.now(timezone.utc)
    return (now + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


# A run baseline whose f000 is exactly "now" (nearest hour) -- so hours >= 0 are "now
# onward" and reliably survive layer_status()'s filtering, whatever the wall clock is
# when the suite runs.
def _current_run():
    run_ts = _reference_now()
    return run_ts.strftime("%Y-%m-%d"), run_ts.strftime("%H")


# --- multi-hour branch (status_product set) ---


def test_layer_status_multi_hour_coverage_percent():
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    now = datetime.now(timezone.utc)
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }
    run_date, run_id = _current_run()
    u.latest_store_run = MagicMock(return_value=(run_date, run_id, [0, 1, 2, 3]))
    # Only hours 0 and 1 are already fully rendered (should_plot_for_hour False).
    u.should_plot_for_hour = MagicMock(side_effect=lambda state, product: state.fhour not in (0, 1))

    result = u.layer_status()

    assert result["kind"] == "layer"
    assert result["percent"] == 50.0
    assert "2/4 hour(s) rendered" in result["detail"]
    assert result["next_update"] is not None


def test_layer_status_multi_hour_zero_percent_with_no_catalog_data():
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    u.process_status_adapter.get_process_status.return_value = None
    u.latest_store_run = MagicMock(return_value=None)

    result = u.layer_status()

    assert result["percent"] == 0.0
    assert result["detail"] is None
    assert result["segments"] is None


def test_layer_status_multi_hour_includes_per_hour_segments():
    """Backs the Data Status page's segmented progress bar -- one {hour, rendered}
    entry per now-onward catalog hour, plus the run_date/run_id they belong to."""
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    now = datetime.now(timezone.utc)
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }
    run_date, run_id = _current_run()
    u.latest_store_run = MagicMock(return_value=(run_date, run_id, [0, 1, 2, 3]))
    u.should_plot_for_hour = MagicMock(side_effect=lambda state, product: state.fhour not in (0, 1))

    result = u.layer_status()

    assert result["run_date"] == run_date
    assert result["run_id"] == run_id
    assert result["segments"] == [
        {"hour": 0, "rendered": True},
        {"hour": 1, "rendered": True},
        {"hour": 2, "rendered": False},
        {"hour": 3, "rendered": False},
    ]


def test_layer_status_segments_are_not_assumed_contiguous():
    """should_plot_for_hour is checked independently per hour, so a scattered
    rendered/pending pattern (not just "first N done") comes through as-is --
    the Data Status page's segmented bar must not assume rendering fills forward."""
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    u.process_status_adapter.get_process_status.return_value = None
    run_date, run_id = _current_run()
    u.latest_store_run = MagicMock(return_value=(run_date, run_id, [0, 1, 2, 3, 4]))
    # Only hours 1 and 3 are rendered -- a scattered, non-contiguous pattern.
    u.should_plot_for_hour = MagicMock(side_effect=lambda state, product: state.fhour not in (1, 3))

    result = u.layer_status()

    assert result["segments"] == [
        {"hour": 0, "rendered": False},
        {"hour": 1, "rendered": True},
        {"hour": 2, "rendered": False},
        {"hour": 3, "rendered": True},
        {"hour": 4, "rendered": False},
    ]


def test_layer_status_multi_hour_excludes_hours_before_now():
    """The catalog retains a window wider than what the scrubber can currently reach
    (data_collector.cache_hours) -- hours before "now" must not count toward percent/
    segments, even though should_plot_for_hour would report them as long since
    rendered. Regression for the Data Status page showing "N hours rendered" for hours
    a user could never actually see on the scrubber."""
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    u.process_status_adapter.get_process_status.return_value = None
    # Baseline 5 hours in the past, so catalog hours 0-4 are all "before now" and only
    # hour 5 onward is reachable.
    baseline_ts = _reference_now() - timedelta(hours=5)
    run_date, run_id = baseline_ts.strftime("%Y-%m-%d"), baseline_ts.strftime("%H")
    u.latest_store_run = MagicMock(return_value=(run_date, run_id, [0, 1, 2, 3, 4, 5, 6]))
    u.should_plot_for_hour = MagicMock(return_value=False)  # everything already rendered

    result = u.layer_status()

    rendered_hours = [seg["hour"] for seg in result["segments"]]
    assert 0 not in rendered_hours
    assert 4 not in rendered_hours
    assert rendered_hours == [5, 6]
    assert result["percent"] == 100.0


def test_layer_status_multi_hour_zero_percent_when_catalog_entirely_stale():
    """If the render backlog has fallen behind "now" entirely (nothing in the catalog
    for status_product reaches the current hour), percent must reflect that gap rather
    than reporting the stale, no-longer-viewable hours as progress."""
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    u.process_status_adapter.get_process_status.return_value = None
    baseline_ts = _reference_now() - timedelta(hours=10)
    run_date, run_id = baseline_ts.strftime("%Y-%m-%d"), baseline_ts.strftime("%H")
    u.latest_store_run = MagicMock(return_value=(run_date, run_id, [0, 1, 2, 3]))
    u.should_plot_for_hour = MagicMock(return_value=False)

    result = u.layer_status()

    assert result["percent"] == 0.0
    assert result["segments"] == []


# --- single-shot branch (status_product is None) ---


def test_layer_status_single_shot_uses_freshness_decay():
    u = make_bare_updater(
        section="sst", settings={"enabled": True, "runs_per_day": 2}, status_product=None
    )
    now = datetime.now(timezone.utc)
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }

    result = u.layer_status()

    assert result["kind"] == "layer"
    assert result["percent"] == 100.0
    assert result["next_update"] == now + timedelta(hours=12)  # 2 runs/day -> period_s=43200
    assert result["segments"] is None


def test_layer_status_single_shot_surfaces_last_error():
    u = make_bare_updater(
        section="sst", settings={"enabled": True, "runs_per_day": 2}, status_product=None
    )
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": None,
        "last_error": "disk full",
    }

    result = u.layer_status()

    assert result["detail"] == "disk full"
