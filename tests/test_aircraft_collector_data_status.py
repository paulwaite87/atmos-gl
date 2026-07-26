#!/usr/bin/env python3
"""Tests for AircraftCollector.data_status()'s coverage-based override (issue #215) --
percent reflects GlobalSampleScheduler's global background-grid freshness
(progress_current/progress_total, written every tick by run() via
record_progress(self.section, ...)), not AsyncCollectorBase's default liveness
heartbeat. Same __new__-bypassing construction test_aircraft_collector_health.py uses."""
from atmos_gl.collectors.aircraft import AircraftCollector
from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter


def make_collector(*, enabled=True):
    c = AircraftCollector.__new__(AircraftCollector)
    c.section = "flightradar_collector"
    c.process_status_adapter = FakeProcessStatusAdapter()
    c.settings = {"enabled": enabled}
    return c


def test_percent_reflects_fresh_over_total_global_regions():
    c = make_collector()
    c.process_status_adapter.record_progress(c.section, "collector", 18, 72)
    status = c.data_status()
    assert status["percent"] == 25.0
    assert status["detail"] == "18/72 global region(s) up to date"


def test_percent_is_zero_before_the_collector_has_ever_ticked():
    """No progress row yet at all -- total=0, so percent is 0.0 (not a divide-by-zero),
    and detail is None (no last_error, and the "N/M" detail only makes sense once a
    real total is known)."""
    c = make_collector()
    status = c.data_status()
    assert status["percent"] == 0.0
    assert status["detail"] is None


def test_full_global_coverage_reads_100_percent():
    c = make_collector()
    c.process_status_adapter.record_progress(c.section, "collector", 72, 72)
    status = c.data_status()
    assert status["percent"] == 100.0
    assert status["detail"] == "72/72 global region(s) up to date"


def test_a_last_error_takes_priority_over_the_coverage_detail_text():
    c = make_collector()
    c.process_status_adapter.record_progress(c.section, "collector", 10, 72)
    c.process_status_adapter.record_process_run(c.section, "collector", success=False, error="timeout")
    status = c.data_status()
    assert status["detail"] == "timeout"
    # percent still reflects coverage, independent of the run failure. build_status()
    # rounds to 1 decimal place.
    assert status["percent"] == round(100.0 * 10 / 72, 1)


def test_health_signal_still_surfaces_through_the_coverage_override():
    c = make_collector()
    c.process_status_adapter.record_progress(c.section, "collector", 10, 72)
    c.process_status_adapter.record_health(c.section, "collector", "rate_limited", "Rate limited (HTTP 429)")
    status = c.data_status()
    assert status["health"] == "rate_limited"
    assert status["health_detail"] == "Rate limited (HTTP 429)"


def test_disabled_collector_reports_no_next_update():
    c = make_collector(enabled=False)
    c.process_status_adapter.record_progress(c.section, "collector", 10, 72)
    status = c.data_status()
    assert status["enabled"] is False
    assert status["next_update"] is None
