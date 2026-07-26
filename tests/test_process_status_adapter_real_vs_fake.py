#!/usr/bin/env python3
"""Guard against ProcessStatusAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeProcessStatusAdapter
hand-reimplements the real adapter's CASE-based upsert SQL in Python independently,
so if they ever diverge, nothing else would catch it. Scoped to the delicate part --
record_process_start()/record_process_run()'s status/started_at transitions, added
alongside the SST mode-switch fix so the Data Status UI can show "running" instead of
a stale reading while a slow collect() (e.g. SST's ~250MB netCDF download) is in flight.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.process_status_adapter import ProcessStatusAdapter, FakeProcessStatusAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return ProcessStatusAdapter(), patch("atmos_gl.db.process_status_adapter.Session", TestSession)
    return FakeProcessStatusAdapter(), contextlib.nullcontext()


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_start_then_run_transitions_through_running_to_terminal_status(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_process_start("sst", "collector")
        running = adapter.get_process_status("sst")
        assert running["status"] == "running"
        assert running["started_at"] is not None
        assert running["last_updated"] is None  # first-ever run -- nothing succeeded yet

        adapter.record_process_run("sst", "collector", success=True)
        done = adapter.get_process_status("sst")
        assert done["status"] == "success"
        assert done["started_at"] is None
        assert done["last_updated"] is not None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_start_does_not_clear_a_prior_error_or_advance_last_updated(kind, real_db):
    """record_process_start() must be non-destructive to the fields a completed run
    already set -- it's marking "work has begun", not resetting history."""
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_process_run("sst", "collector", success=False, error="503")
        before = adapter.get_process_status("sst")

        adapter.record_process_start("sst", "collector")
        during = adapter.get_process_status("sst")

        assert during["status"] == "running"
        assert during["last_error"] == before["last_error"]
        assert during["last_updated"] == before["last_updated"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_run_failure_after_start_clears_started_at_and_sets_failed(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_process_start("sst", "collector")
        adapter.record_process_run("sst", "collector", success=False, error="timeout")
        row = adapter.get_process_status("sst")

        assert row["status"] == "failed"
        assert row["started_at"] is None
        assert row["last_error"] == "timeout"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_record_process_run_does_not_clobber_an_existing_health_signal(kind, real_db):
    """record_health()'s three columns are deliberately untouched by
    record_process_run()/record_process_start() -- a handful of throttled requests
    doesn't mean the collector's whole run failed. Scoped here (not just the Fake's
    own tests) because this is exactly the class of Real/Fake conditional-update
    drift test_ship_adapter_real_vs_fake.py was created to catch."""
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_health("flightradar_collector", "collector", "rate_limited", "Rate limited (HTTP 429)")
        adapter.record_process_run("flightradar_collector", "collector", success=True)
        row = adapter.get_process_status("flightradar_collector")

        assert row["status"] == "success"
        assert row["health"] == "rate_limited"
        assert row["health_detail"] == "Rate limited (HTTP 429)"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_record_health_none_clears_a_prior_health_signal(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_health("flightradar_collector", "collector", "blocked", "Blocked (HTTP 529)")
        adapter.record_health("flightradar_collector", "collector", None)
        row = adapter.get_process_status("flightradar_collector")

        assert row["health"] is None
        assert row["health_detail"] is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_record_progress_survives_a_subsequent_process_run(kind, real_db):
    """record_progress()'s two columns are deliberately untouched by
    record_process_run()/record_process_start()/record_health() -- same class of
    conditional-update drift as the health tests above."""
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_progress("flightradar_hotspot", "layer", 3, 8)
        adapter.record_process_run("flightradar_hotspot", "layer", success=True)
        row = adapter.get_process_status("flightradar_hotspot")

        assert row["status"] == "success"
        assert row["progress_current"] == 3
        assert row["progress_total"] == 8
