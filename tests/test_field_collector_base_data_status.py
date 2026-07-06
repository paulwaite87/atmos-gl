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
    # run_id is only ever whole-hour precision (real GFS run_ids are "00"/"06"/"12"/"18"),
    # so fhour_0 = round((now - run_ts) / 3600) depends on where `now` falls within its own
    # hour. Compute fhour_0 the same way data_status() does, rather than assuming a fixed
    # value -- a hardcoded assumption here made this test flaky depending on the wall-clock
    # minute at run time.
    run_ts = (now - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    run_date = run_ts.strftime("%Y-%m-%d")
    run_id = run_ts.strftime("%H")
    fhour_0 = max(0, round((now - run_ts).total_seconds() / 3600.0))
    fhour_end = fhour_0 + 4  # cache_hours
    # 2 of the 4 expected hours [fhour_0, fhour_end) are present; 2 more outside it aren't.
    hours = [fhour_0, fhour_0 + 1, fhour_end + 10, fhour_end + 11]
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": run_date,
        "run_id": run_id,
        "hours": hours,
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
