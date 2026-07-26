#!/usr/bin/env python3
"""Real-DB test for get_fleet_as_geojson()'s route join (issue #215's route-lookup
follow-on) -- this specific behavior (a LEFT JOIN across aircraft and flight_route)
can't be verified through FakeAircraftAdapter, which deliberately knows nothing about
flight_route at all (see AircraftAdapter.get_active_flight_positions's docstring)."""
import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.aircraft_adapter import AircraftAdapter
from atmos_gl.db.flight_route_adapter import FlightRouteAdapter


@pytest.fixture
def aircraft_adapter(real_db):
    TestSession = sessionmaker(bind=real_db)
    with patch("atmos_gl.db.aircraft_adapter.Session", TestSession):
        yield AircraftAdapter()


@pytest.fixture
def flight_route_adapter(real_db):
    TestSession = sessionmaker(bind=real_db)
    with patch("atmos_gl.db.flight_route_adapter.Session", TestSession):
        yield FlightRouteAdapter()


def _sighting(hex="a1b2c3", flight="GEOJNMATCH1", **overrides):
    base = {
        "hex": hex, "flight": flight, "r": "ZK-TST", "t": "B738",
        "lat": -41.3, "lon": 174.8, "alt_baro": 5000, "gs": 200.0, "track": 0.0,
        "baro_rate": 0, "nav_altitude_mcp": None, "squawk": "2000",
    }
    base.update(overrides)
    return base


def test_geojson_includes_the_joined_route_when_one_exists(aircraft_adapter, flight_route_adapter):
    aircraft_adapter.record_sighting(_sighting())
    stops = [{"icao": "NZWN", "iata": "WGN"}, {"icao": "NZAA", "iata": "AKL"}]
    flight_route_adapter.record_routes({"GEOJNMATCH1": {"stops": stops, "plausible": True}})

    collection = json.loads(aircraft_adapter.get_fleet_as_geojson())
    props = collection["features"][0]["properties"]
    assert props["route_stops"] == stops
    assert props["route_plausible"] is True


def test_geojson_route_fields_are_null_when_no_flight_route_row_exists(aircraft_adapter):
    aircraft_adapter.record_sighting(_sighting(flight="GEOJNUNMATCHED1"))
    collection = json.loads(aircraft_adapter.get_fleet_as_geojson())
    props = collection["features"][0]["properties"]
    assert props["route_stops"] is None
    assert props["route_plausible"] is None


def test_geojson_route_fields_are_null_when_the_stored_route_is_a_confirmed_no_match(
    aircraft_adapter, flight_route_adapter
):
    aircraft_adapter.record_sighting(_sighting(flight="GEOJNNOMATCH1"))
    flight_route_adapter.record_routes({"GEOJNNOMATCH1": None})

    collection = json.loads(aircraft_adapter.get_fleet_as_geojson())
    props = collection["features"][0]["properties"]
    assert props["route_stops"] is None
    assert props["route_plausible"] is None
