#!/usr/bin/env python3
"""Tests for QuakeCollector.collect() -- previously untested: the inline requests.get
call made it awkward to mock before CollectorBase._get() gave it a shared, patchable
seam (architecture review candidate "shared GET-fetch module")."""
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.collectors.quakes import QuakeCollector

_CSV = (
    "id,mag,depth,place,time,latitude,longitude\n"
    "us1,4.2,10.0,Somewhere,2026-08-21T12:00:00.000Z,10.0,20.0\n"
    "us2,2.1,5.0,Elsewhere,2026-08-21T13:00:00.000Z,11.0,21.0\n"
)


def make_collector(min_mag=3.5, url="http://example.com/quakes.csv"):
    c = QuakeCollector.__new__(QuakeCollector)
    c.settings = {"min_mag": min_mag}
    c.quake_adapter = MagicMock()
    c.datasource_url = MagicMock(return_value=url)
    return c


def test_collect_upserts_rows_at_or_above_min_mag():
    c = make_collector(min_mag=3.5)
    with patch.object(CollectorBase, "_get", return_value=MagicMock(text=_CSV)):
        c.collect()

    c.quake_adapter.update_quake.assert_called_once()
    args = c.quake_adapter.update_quake.call_args[0]
    assert args[0] == "us1"
    assert args[1] == 4.2


def test_collect_skips_when_no_url_configured():
    c = make_collector(url="")
    with patch.object(CollectorBase, "_get") as mock_get:
        c.collect()

    mock_get.assert_not_called()
    c.quake_adapter.update_quake.assert_not_called()


def test_collect_logs_and_returns_when_fetch_fails():
    c = make_collector()
    with patch.object(CollectorBase, "_get", return_value=None):
        c.collect()  # must not raise

    c.quake_adapter.update_quake.assert_not_called()
