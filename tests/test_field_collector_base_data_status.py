#!/usr/bin/env python3
"""Regression test for FieldCollectorBase.data_status() after routing it through
lib/data_status.py (architecture review candidate "one status module") -- locks the
dict shape and the coverage-based percent calculation, since this had no test coverage
before this refactor.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from worldmap.collectors.field_base import FieldCollectorBase


def make_bare_field_collector(status_name="gfs_atmos", settings=None, products=None):
    c = FieldCollectorBase.__new__(FieldCollectorBase)
    c.status_name = status_name
    c.settings = settings or {}
    c.products = products or {"wind": object()}
    c.process_status_adapter = MagicMock()
    c.store = MagicMock()
    return c


def test_field_collector_base_data_status_no_catalog_data_yet():
    c = make_bare_field_collector(settings={"enabled": True})
    c.process_status_adapter.get_process_status.return_value = None
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

    result = c.data_status()

    assert result["name"] == "gfs_atmos"
    assert result["kind"] == "collector"
    assert result["percent"] == 0.0
    assert result["last_updated"] is None


def test_field_collector_base_data_status_coverage_percent():
    c = make_bare_field_collector(
        status_name="gfs_atmos",
        settings={"enabled": True, "cache_hours": 4},
        products={"wind": object()},
    )
    now = datetime.now(timezone.utc)
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": now,
        "last_error": None,
    }
    run_ts = now - timedelta(hours=1)
    run_date = run_ts.strftime("%Y-%m-%d")
    run_id = run_ts.strftime("%H")
    # fhour_0 ~= 1 (now is ~1h after the run), expected window [1, 1+cache_hours=5) -> 4 hours.
    # Only 2 of those 4 expected hours are actually present in the catalog.
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": run_date,
        "run_id": run_id,
        "hours": [1, 2, 10, 11],
    }

    result = c.data_status()

    assert result["percent"] == 50.0
    assert "2/4 hour(s)" in result["detail"]


def test_field_collector_base_data_status_last_error_takes_priority_over_computed_detail():
    c = make_bare_field_collector(settings={"enabled": True})
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None,
        "last_error": "NOMADS unreachable",
    }
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

    result = c.data_status()

    assert result["detail"] == "NOMADS unreachable"
