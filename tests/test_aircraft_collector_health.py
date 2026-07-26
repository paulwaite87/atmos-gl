#!/usr/bin/env python3
"""Tests for AircraftCollector._report_status()'s HTTP-status classification (issue
#215's Data Status health-icon feature) -- the same narrow, __new__-bypassing
construction shipping/lightning's own collector tests use, so this doesn't need a real
config file or DB connection."""
from atmos_gl.collectors.aircraft import AircraftCollector
from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter


def make_collector():
    c = AircraftCollector.__new__(AircraftCollector)
    c.section = "flightradar_collector"
    c.process_status_adapter = FakeProcessStatusAdapter()
    return c


def test_report_status_429_sets_rate_limited():
    c = make_collector()
    c._report_status(429)
    row = c.process_status_adapter.get_process_status("flightradar_collector")
    assert row["health"] == "rate_limited"
    assert row["health_detail"] == "Rate limited (HTTP 429)"


def test_report_status_5xx_sets_blocked_with_the_status_code():
    c = make_collector()
    c._report_status(529)
    row = c.process_status_adapter.get_process_status("flightradar_collector")
    assert row["health"] == "blocked"
    assert row["health_detail"] == "Blocked (HTTP 529)"


def test_report_status_other_4xx_sets_blocked():
    c = make_collector()
    c._report_status(403)
    row = c.process_status_adapter.get_process_status("flightradar_collector")
    assert row["health"] == "blocked"
    assert row["health_detail"] == "Blocked (HTTP 403)"


def test_report_status_200_does_not_record_health():
    """The healthy path is deliberately silent -- read_health_status()'s read-time TTL
    expiry (lib/data_status.py) is what reverts the icon to "ok", not an explicit
    clear-on-success write."""
    c = make_collector()
    c._report_status(200)
    assert c.process_status_adapter.get_process_status("flightradar_collector") is None


def test_report_status_does_not_clobber_a_more_specific_prior_health_signal_incorrectly():
    """A later 200 must not need to explicitly clear a rate_limited signal set moments
    earlier -- it simply does nothing, leaving the existing signal to decay via TTL."""
    c = make_collector()
    c._report_status(429)
    c._report_status(200)
    row = c.process_status_adapter.get_process_status("flightradar_collector")
    assert row["health"] == "rate_limited"
