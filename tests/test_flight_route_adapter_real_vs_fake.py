#!/usr/bin/env python3
"""Guard against FlightRouteAdapter Real/Fake drift (issue #215's route-lookup
follow-on), matching the pattern test_process_status_adapter_real_vs_fake.py and
test_ship_adapter_real_vs_fake.py established: FakeFlightRouteAdapter hand-reimplements
the real adapter's upsert/staleness SQL in Python independently, so if they ever
diverge, nothing else would catch it."""
import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.flight_route_adapter import FlightRouteAdapter, FakeFlightRouteAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return FlightRouteAdapter(), patch("atmos_gl.db.flight_route_adapter.Session", TestSession)
    return FakeFlightRouteAdapter(), contextlib.nullcontext()


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_filter_stale_treats_an_unknown_callsign_as_stale(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        assert adapter.filter_stale(["RVFUNKNOWN1"], backstop_days=7) == ["RVFUNKNOWN1"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_filter_stale_excludes_a_recently_recorded_match(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_routes({"RVFMATCH1": {"stops": [{"icao": "NZWN", "iata": "WGN"}], "plausible": True}})
        assert adapter.filter_stale(["RVFMATCH1"], backstop_days=7) == []


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_filter_stale_reincludes_a_callsign_past_the_backstop(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        old = datetime.now(timezone.utc) - timedelta(days=10)
        adapter.record_routes({"RVFBACKSTOP1": {"stops": [], "plausible": True}}, now=old)
        assert adapter.filter_stale(["RVFBACKSTOP1"], backstop_days=7) == ["RVFBACKSTOP1"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_filter_stale_applies_the_same_backstop_to_a_confirmed_no_match(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_routes({"RVFNOMATCH1": None})
        assert adapter.filter_stale(["RVFNOMATCH1"], backstop_days=7) == []

        old = datetime.now(timezone.utc) - timedelta(days=10)
        adapter.record_routes({"RVFNOMATCH1": None}, now=old)
        assert adapter.filter_stale(["RVFNOMATCH1"], backstop_days=7) == ["RVFNOMATCH1"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_record_routes_upsert_keeps_a_callsign_fresh_across_repeated_writes(kind, real_db):
    """A second record_routes() call for the same callsign (e.g. a later enrichment
    tick re-confirming it) must UPDATE the existing row, not insert a conflicting
    second one -- filter_stale() staying empty after two writes is exactly what a
    broken ON CONFLICT clause (or a Fake that appended instead of overwrote) would
    get wrong."""
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.record_routes({"RVFUPSERT1": {"stops": [{"icao": "NZWN", "iata": "WGN"}], "plausible": True}})
        adapter.record_routes({"RVFUPSERT1": {"stops": [{"icao": "NZWN", "iata": "WGN"}, {"icao": "NZAA", "iata": "AKL"}], "plausible": True}})
        assert adapter.filter_stale(["RVFUPSERT1"], backstop_days=7) == []
