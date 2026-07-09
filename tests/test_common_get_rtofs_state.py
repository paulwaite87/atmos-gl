#!/usr/bin/env python3
"""Smoke test for Updater.get_rtofs_state's delegation to lib.rtofs.resolve_rtofs_baseline().

get_rtofs_state and get_gfs_state now share _resolve_forecast_state (architecture
review candidate "slim Updater; delete dead code") -- this locks in RTOFS's own
baseline-cache-or-fetch behaviour, which had no test coverage before that merge,
mirroring tests/test_common_get_gfs_state.py.
"""
import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import patch

from atmos_gl.tasks.common import Updater

FIXED_NOW = real_datetime.datetime(2026, 1, 1, 6, 0, 0, tzinfo=real_datetime.timezone.utc)


def make_bare_updater(forecast_hour=0):
    updater = Updater.__new__(Updater)
    updater.section = "test"
    updater.forecast_hour = forecast_hour
    updater.map_data = SimpleNamespace(shared_state={})
    return updater


def test_get_rtofs_state_resolves_and_computes_offset():
    baseline = {
        "date_str": "20260101",
        "date_str_Y_M_D": "2026-01-01",
        "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=3),
    }
    updater = make_bare_updater(forecast_hour=0)

    with (
        patch("atmos_gl.lib.rtofs.resolve_rtofs_baseline", return_value=baseline) as mock_resolve,
        patch("atmos_gl.tasks.common.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = FIXED_NOW
        state = updater.get_rtofs_state()

    mock_resolve.assert_called_once()
    assert state.forecast_hour_str == "003"  # 3h since run + 0h offset
    assert state.run_date_str == "20260101"
    assert state.run_id == "00"
    assert updater.map_data.shared_state["rtofs_baseline"] == baseline


def test_get_rtofs_state_caches_baseline_across_calls():
    baseline = {
        "date_str": "20260101",
        "date_str_Y_M_D": "2026-01-01",
        "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=1),
    }
    updater = make_bare_updater(forecast_hour=2)

    with (
        patch("atmos_gl.lib.rtofs.resolve_rtofs_baseline", return_value=baseline) as mock_resolve,
        patch("atmos_gl.tasks.common.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = FIXED_NOW
        updater.get_rtofs_state()
        state = updater.get_rtofs_state()

    mock_resolve.assert_called_once()  # second call reads shared_state, doesn't re-probe
    assert state.forecast_hour_str == "003"  # 1h since run + 2h offset


def test_get_rtofs_state_raises_when_baseline_unresolvable():
    updater = make_bare_updater()

    with patch("atmos_gl.lib.rtofs.resolve_rtofs_baseline", return_value=None):
        try:
            updater.get_rtofs_state()
        except RuntimeError as e:
            assert "Failed to sync RTOFS baseline" in str(e)
        else:
            raise AssertionError("expected RuntimeError when baseline can't be resolved")


def test_gfs_and_rtofs_baselines_are_cached_independently():
    """Both methods now share _resolve_forecast_state -- confirms they still key off
    distinct shared_state entries rather than clobbering each other."""
    gfs_baseline = {
        "date_str": "20260101", "date_str_Y_M_D": "2026-01-01", "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=1),
    }
    rtofs_baseline = {
        "date_str": "20260102", "date_str_Y_M_D": "2026-01-02", "run": "00",
        "timestamp": FIXED_NOW - real_datetime.timedelta(hours=2),
    }
    updater = make_bare_updater()

    with (
        patch("atmos_gl.lib.gfs.resolve_gfs_baseline", return_value=gfs_baseline),
        patch("atmos_gl.lib.rtofs.resolve_rtofs_baseline", return_value=rtofs_baseline),
        patch("atmos_gl.tasks.common.datetime") as mock_datetime,
    ):
        mock_datetime.now.return_value = FIXED_NOW
        gfs_state = updater.get_gfs_state()
        assert gfs_state.run_date_str == "20260101"
        rtofs_state = updater.get_rtofs_state()
        assert rtofs_state.run_date_str == "20260102"

    assert updater.map_data.shared_state["gfs_baseline"] == gfs_baseline
    assert updater.map_data.shared_state["rtofs_baseline"] == rtofs_baseline
