#!/usr/bin/env python3
"""Regression tests for Updater.layer_status() after routing it through
lib/data_status.py (architecture review candidate "one status module") -- locks both
branches' dict shape and percent calculation, since this had no test coverage before
this refactor.
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


# --- multi-hour branch (status_product set) ---


def test_layer_status_multi_hour_coverage_percent():
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    now = datetime.now(timezone.utc)
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }
    u.latest_store_run = MagicMock(return_value=("2026-06-13", "18", [0, 1, 2, 3]))
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
    entry per catalog hour, plus the run_date/run_id they belong to."""
    u = make_bare_updater(section="isobars", settings={"enabled": True}, status_product="isobars")
    now = datetime.now(timezone.utc)
    u.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }
    u.latest_store_run = MagicMock(return_value=("2026-06-13", "18", [0, 1, 2, 3]))
    u.should_plot_for_hour = MagicMock(side_effect=lambda state, product: state.fhour not in (0, 1))

    result = u.layer_status()

    assert result["run_date"] == "2026-06-13"
    assert result["run_id"] == "18"
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
    u.latest_store_run = MagicMock(return_value=("2026-06-13", "18", [0, 1, 2, 3, 4]))
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
