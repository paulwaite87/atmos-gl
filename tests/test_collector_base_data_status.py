#!/usr/bin/env python3
"""Regression tests for CollectorBase.data_status() and AsyncCollectorBase.data_status()
after routing them through lib/data_status.py (architecture review candidate "one status
module") -- locks the dict shape and percent/next_update values these produce, since
neither had any test coverage before this refactor.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from atmos_gl.collectors.base import CollectorBase, AsyncCollectorBase


def make_bare_collector(section="quakes", settings=None):
    c = CollectorBase.__new__(CollectorBase)
    c.section = section
    c.settings = settings or {}
    c.process_status_adapter = MagicMock()
    return c


def make_bare_async_collector(section="shipping_collector", settings=None, heartbeat_period_s=300.0):
    c = AsyncCollectorBase.__new__(AsyncCollectorBase)
    c.section = section
    c.settings = settings or {}
    c.heartbeat_period_s = heartbeat_period_s
    c.process_status_adapter = MagicMock()
    return c


def test_collector_base_data_status_shape_with_no_row_yet():
    c = make_bare_collector(settings={"enabled": True, "runs_per_day": 24})
    c.process_status_adapter.get_process_status.return_value = None

    result = c.data_status()

    assert result["name"] == "quakes"
    assert result["kind"] == "collector"
    assert result["percent"] == 0.0
    assert result["last_updated"] is None
    assert result["enabled"] is True
    assert result["detail"] is None
    assert result["next_update"] is not None  # enabled + never run -> estimated


def test_collector_base_data_status_reflects_a_fresh_run():
    c = make_bare_collector(settings={"enabled": True, "runs_per_day": 24})
    now = datetime.now(timezone.utc)
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }

    result = c.data_status()

    assert result["percent"] == 100.0
    assert result["last_updated"] == now
    assert result["next_update"] == now + timedelta(hours=1)  # 24 runs/day -> period_s=3600


def test_collector_base_data_status_surfaces_last_error_as_detail():
    c = make_bare_collector(settings={"enabled": True, "runs_per_day": 24})
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None,
        "last_error": "connection refused",
    }

    result = c.data_status()

    assert result["detail"] == "connection refused"


def test_async_collector_base_data_status_uses_heartbeat_period_s():
    c = make_bare_async_collector(
        settings={"enabled": True}, heartbeat_period_s=600.0
    )
    now = datetime.now(timezone.utc)
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }

    result = c.data_status()

    assert result["percent"] == 100.0
    assert result["next_update"] == now + timedelta(seconds=600.0)


def test_async_collector_base_data_status_next_update_none_when_disabled():
    c = make_bare_async_collector(settings={"enabled": False})
    c.process_status_adapter.get_process_status.return_value = None

    result = c.data_status()

    assert result["next_update"] is None
    assert result["enabled"] is False


# --- source_url() (Data Status page's clickable-label link) ---


def test_collector_base_source_url_none_when_no_datasource_key():
    c = make_bare_collector()
    c.datasource_key = ""

    assert c.source_url() is None


def test_collector_base_source_url_reads_configured_datasource():
    c = make_bare_collector()
    c.datasource_key = "quakes"
    c.config = MagicMock()
    c.config.get_setting.return_value = {"quakes": "https://example.com/quakes.csv"}

    assert c.source_url() == "https://example.com/quakes.csv"
    c.config.get_setting.assert_called_once_with("data_collector", "datasources", {})


def test_collector_base_source_url_none_when_datasource_key_not_configured():
    c = make_bare_collector()
    c.datasource_key = "quakes"
    c.config = MagicMock()
    c.config.get_setting.return_value = {}

    assert c.source_url() is None


def test_async_collector_base_source_url_reads_configured_datasource():
    c = make_bare_async_collector()
    c.datasource_key = "shipping"
    c.config = MagicMock()
    c.config.get_setting.return_value = {"shipping": "wss://stream.aisstream.io/v0/stream"}

    assert c.source_url() == "wss://stream.aisstream.io/v0/stream"


def test_async_collector_base_source_url_none_when_no_datasource_key():
    c = make_bare_async_collector()
    c.datasource_key = ""

    assert c.source_url() is None
