#!/usr/bin/env python3
"""Tests for RtofsCurrentsCollector's SingleFileFieldCollector hooks:
_resolve_download_url()'s nowcast fallback (unconditional from backfill_hour(),
fhour_0-only from collect()'s loop -- see its docstring) and _guard_cycle()'s f072
abort.
"""
from unittest.mock import patch

from atmos_gl.collectors.rtofs_currents import RtofsCurrentsCollector, RTOFS_MAX_HOURLY_FHOUR


def make_collector():
    return RtofsCurrentsCollector.__new__(RtofsCurrentsCollector)


@patch("atmos_gl.collectors.rtofs_currents.remote_exists", return_value=True)
def test_returns_forecast_url_when_published(mock_exists):
    c = make_collector()

    url = c._resolve_download_url("https://base", "2026-07-15", "00", 5, allow_fallback=False)

    assert url is not None
    assert "f005" in url
    mock_exists.assert_called_once()  # forecast published -- no need to probe the nowcast


@patch("atmos_gl.collectors.rtofs_currents.remote_exists")
def test_falls_back_to_nowcast_when_allowed(mock_exists):
    mock_exists.side_effect = [False, True]  # forecast missing, nowcast present

    c = make_collector()
    url = c._resolve_download_url("https://base", "2026-07-15", "00", 5, allow_fallback=True)

    assert url is not None
    assert "n000" in url


@patch("atmos_gl.collectors.rtofs_currents.remote_exists")
def test_does_not_fall_back_when_disallowed(mock_exists):
    mock_exists.side_effect = [False, True]  # forecast missing, nowcast would be present

    c = make_collector()
    url = c._resolve_download_url("https://base", "2026-07-15", "00", 5, allow_fallback=False)

    assert url is None


def test_guard_cycle_aborts_past_hourly_limit():
    c = make_collector()

    assert c._guard_cycle(RTOFS_MAX_HOURLY_FHOUR + 1, RTOFS_MAX_HOURLY_FHOUR + 5) is False


def test_guard_cycle_proceeds_within_hourly_limit():
    c = make_collector()

    assert c._guard_cycle(RTOFS_MAX_HOURLY_FHOUR, RTOFS_MAX_HOURLY_FHOUR + 1) is True
