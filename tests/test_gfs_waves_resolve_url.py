#!/usr/bin/env python3
"""Tests for GfsWavesCollector._resolve_download_url() -- the SingleFileFieldCollector
hook encapsulating waves' remote-exists check. No fallback here, unlike
RtofsCurrentsCollector's nowcast (waves has no equivalent "present conditions" file).
"""
from unittest.mock import patch

from atmos_gl.collectors.gfs_waves import GfsWavesCollector


def make_collector():
    return GfsWavesCollector.__new__(GfsWavesCollector)


@patch("atmos_gl.collectors.gfs_waves.remote_exists", return_value=True)
def test_returns_url_when_published(mock_exists):
    c = make_collector()

    url = c._resolve_download_url("https://base", "2026-07-15", "00", 3, allow_fallback=False)

    assert url is not None
    assert "f003" in url
    mock_exists.assert_called_once()


@patch("atmos_gl.collectors.gfs_waves.remote_exists", return_value=False)
def test_returns_none_when_not_published(mock_exists):
    c = make_collector()

    url = c._resolve_download_url("https://base", "2026-07-15", "00", 3, allow_fallback=True)

    assert url is None
