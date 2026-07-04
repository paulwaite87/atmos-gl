#!/usr/bin/env python3
"""Smoke test for Updater.get_gfs_state's delegation to lib.gfs.resolve_gfs_baseline().

Constructs a bare Updater (bypassing __init__, which needs a real config/fieldstore)
and stubs the network-facing resolve_gfs_baseline so no NOMADS access happens.
"""
import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import patch

from worldmap.tasks.common import Updater

FIXED_NOW = real_datetime.datetime(2026, 1, 1, 6, 0, 0, tzinfo=real_datetime.timezone.utc)


def make_bare_updater(forecast_hour=0):
    updater = Updater.__new__(Updater)
    updater.section = "test"
    updater.forecast_hour = forecast_hour
    updater.map_data = SimpleNamespace(shared_state={})
    return updater


def test_get_gfs_state_resolves_and_computes_offset():
    baseline = {
        "date_str": "20260101",
        "date_str_Y_M_D": "2026-01-01",
        "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=3),
    }
    updater = make_bare_updater(forecast_hour=0)

    with (
        patch("worldmap.lib.gfs.resolve_gfs_baseline", return_value=baseline) as mock_resolve,
        patch("worldmap.tasks.common.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = FIXED_NOW
        updater.get_gfs_state()

    mock_resolve.assert_called_once()
    assert updater.forecast_hour_str == "003"  # 3h since run + 0h offset
    assert updater.run_date_str == "20260101"
    assert updater.run_date_str_Y_M_D == "2026-01-01"
    assert updater.run_id == "00"
    assert updater.map_data.shared_state["gfs_baseline"] == baseline


def test_get_gfs_state_caches_baseline_across_calls():
    baseline = {
        "date_str": "20260101",
        "date_str_Y_M_D": "2026-01-01",
        "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=1),
    }
    updater = make_bare_updater(forecast_hour=2)

    with (
        patch("worldmap.lib.gfs.resolve_gfs_baseline", return_value=baseline) as mock_resolve,
        patch("worldmap.tasks.common.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = FIXED_NOW
        updater.get_gfs_state()
        updater.get_gfs_state()

    mock_resolve.assert_called_once()  # second call reads shared_state, doesn't re-probe
    assert updater.forecast_hour_str == "003"  # 1h since run + 2h offset


def test_get_gfs_state_raises_when_baseline_unresolvable():
    updater = make_bare_updater()

    with patch("worldmap.lib.gfs.resolve_gfs_baseline", return_value=None):
        try:
            updater.get_gfs_state()
        except RuntimeError as e:
            assert "Failed to sync GFS baseline" in str(e)
        else:
            raise AssertionError("expected RuntimeError when baseline can't be resolved")
