#!/usr/bin/env python3
"""Tests for SatellitesCollector.source_url() -- architecture review Candidate 2:
collect()/has_new_data() always had a working hardcoded CelesTrak fallback that the
inherited source_url() (Data Status link) didn't share, so an unconfigured
datasources.satellites key showed a blank label while collection kept working fine.
Now source_url() carries the same fallback, and has_new_data()/_fetch_group() call it
directly instead of maintaining a separate _base_url().
"""
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.satellites import SatellitesCollector


def make_collector(url=None):
    c = SatellitesCollector.__new__(SatellitesCollector)
    c.config = MagicMock()
    c.config.get_setting.return_value = {"satellites": url} if url else {}
    return c


def test_source_url_returns_configured_value():
    c = make_collector(url="https://custom.example/celestrak")
    assert c.source_url() == "https://custom.example/celestrak"


def test_source_url_falls_back_to_celestrak_default_when_unconfigured():
    c = make_collector()
    assert c.source_url() == "https://celestrak.org/NORAD/elements"


def test_has_new_data_builds_url_from_source_url():
    c = make_collector(url="https://custom.example/celestrak")
    c._head_changed_or_default = MagicMock(return_value=True)

    c.has_new_data()

    c._head_changed_or_default.assert_called_once_with(
        "https://custom.example/celestrak/gp.php?GROUP=stations&FORMAT=json", "Satellites"
    )


@patch("atmos_gl.collectors.satellites.requests.get")
def test_fetch_group_uses_the_fallback_when_unconfigured(mock_get):
    c = make_collector()
    mock_get.return_value = MagicMock(status_code=200, json=lambda: [])

    c._fetch_group("stations")

    mock_get.assert_called_once()
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json"
