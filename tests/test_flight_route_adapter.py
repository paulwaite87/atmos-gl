#!/usr/bin/env python3
"""Tests for FakeFlightRouteAdapter (issue #215's route-lookup follow-on) -- keyed on
callsign, deliberately independent of Aircraft/AircraftInterest (see
AircraftAdapter.get_active_flight_positions for the priority-ordered candidate feed
this adapter's filter_stale() narrows down)."""
from datetime import datetime, timedelta, timezone

from atmos_gl.db.flight_route_adapter import FakeFlightRouteAdapter


def test_filter_stale_treats_an_unknown_callsign_as_stale():
    adapter = FakeFlightRouteAdapter()
    assert adapter.filter_stale(["ANZ423"], backstop_days=7) == ["ANZ423"]


def test_filter_stale_excludes_a_recently_checked_callsign():
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({"ANZ423": {"stops": [], "plausible": True}})
    assert adapter.filter_stale(["ANZ423"], backstop_days=7) == []


def test_filter_stale_reincludes_a_callsign_past_the_backstop():
    adapter = FakeFlightRouteAdapter()
    old = datetime.now(timezone.utc) - timedelta(days=10)
    adapter.record_routes({"ANZ423": {"stops": [], "plausible": True}}, now=old)
    assert adapter.filter_stale(["ANZ423"], backstop_days=7) == ["ANZ423"]


def test_filter_stale_applies_the_same_backstop_to_a_confirmed_no_match():
    """Q4: a confirmed no-match (route=None) uses the exact same 7-day backstop as a
    real match -- no separate no-match-specific retry policy."""
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({"N12345": None})
    assert adapter.filter_stale(["N12345"], backstop_days=7) == []

    old = datetime.now(timezone.utc) - timedelta(days=10)
    adapter.record_routes({"N12345": None}, now=old)
    assert adapter.filter_stale(["N12345"], backstop_days=7) == ["N12345"]


def test_filter_stale_preserves_input_order():
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({"BBB222": {"stops": [], "plausible": True}})
    assert adapter.filter_stale(["AAA111", "BBB222", "CCC333"], backstop_days=7) == [
        "AAA111", "CCC333",
    ]


def test_filter_stale_of_an_empty_list_is_empty():
    adapter = FakeFlightRouteAdapter()
    assert adapter.filter_stale([], backstop_days=7) == []


def test_record_routes_stores_stops_and_plausible_for_a_match():
    adapter = FakeFlightRouteAdapter()
    stops = [{"icao": "NZWN", "iata": "WGN"}, {"icao": "NZAA", "iata": "AKL"}]
    adapter.record_routes({"ANZ423": {"stops": stops, "plausible": True}})
    row = adapter._routes["ANZ423"]
    assert row["stops"] == stops
    assert row["plausible"] is True
    assert row["checked_at"] is not None


def test_record_routes_stores_null_stops_for_a_confirmed_no_match():
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({"N12345": None})
    row = adapter._routes["N12345"]
    assert row["stops"] is None
    assert row["plausible"] is None
    assert row["checked_at"] is not None


def test_record_routes_overwrites_a_prior_entry_for_the_same_callsign():
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({"ANZ423": {"stops": [{"icao": "NZWN"}], "plausible": True}})
    adapter.record_routes({"ANZ423": None})
    assert adapter._routes["ANZ423"]["stops"] is None


def test_record_routes_of_an_empty_dict_is_a_no_op():
    adapter = FakeFlightRouteAdapter()
    adapter.record_routes({})
    assert adapter._routes == {}
