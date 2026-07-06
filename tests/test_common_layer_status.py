#!/usr/bin/env python3
"""Regression tests for Updater.layer_status() after routing it through
lib/data_status.py (architecture review candidate "one status module") -- locks both
branches' dict shape and percent calculation, since this had no test coverage before
this refactor.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from worldmap.tasks.common import Updater


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
    u.should_plot_for_hour = MagicMock(side_effect=lambda product, fh: fh not in (0, 1))

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
