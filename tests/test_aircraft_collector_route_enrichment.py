#!/usr/bin/env python3
"""Tests for AircraftCollector's route-enrichment tick (issue #215's route-lookup
follow-on) -- _enrich_routes() and its settings, plus the tick-gate math in run().
Same __new__-bypassing construction test_aircraft_collector_health.py/
test_aircraft_collector_data_status.py use, so no real config file or DB connection
is needed."""
import pytest

from atmos_gl.collectors.aircraft import AircraftCollector
from atmos_gl.db.aircraft_adapter import FakeAircraftAdapter
from atmos_gl.db.flight_route_adapter import FakeFlightRouteAdapter


def make_collector(settings=None):
    c = AircraftCollector.__new__(AircraftCollector)
    c.section = "flightradar_collector"
    c.settings = settings or {}
    c.aircraft_adapter = FakeAircraftAdapter()
    c.flight_route_adapter = FakeFlightRouteAdapter()
    c.routeset_base_url = "https://api.adsb.lol/api/0/routeset"
    return c


# ---- settings methods: defaults + clamping ---------------------------------------

def test_route_enrichment_interval_defaults_to_60s():
    c = make_collector()
    assert c._route_enrichment_interval_seconds() == 60.0


def test_route_enrichment_interval_is_floored_at_5s():
    c = make_collector({"route_enrichment_interval_seconds": 1})
    assert c._route_enrichment_interval_seconds() == 5.0


def test_route_batch_size_defaults_to_25():
    c = make_collector()
    assert c._route_batch_size() == 25


def test_route_batch_size_is_capped_at_the_server_hard_limit():
    """ROUTESET_BATCH_LIMIT (100) is adsblol/api's own real cap, verified against its
    source -- not a guessed ceiling."""
    c = make_collector({"route_batch_size": 500})
    assert c._route_batch_size() == 100


def test_route_batch_size_is_floored_at_1():
    c = make_collector({"route_batch_size": 0})
    assert c._route_batch_size() == 1


def test_route_backstop_days_defaults_to_7():
    c = make_collector()
    assert c._route_backstop_days() == 7.0


def test_settings_fall_back_to_defaults_on_bad_config_values():
    c = make_collector({
        "route_enrichment_interval_seconds": "not-a-number",
        "route_batch_size": "also-bad",
        "route_backstop_days": None,
    })
    assert c._route_enrichment_interval_seconds() == 60.0
    assert c._route_batch_size() == 25
    assert c._route_backstop_days() == 7.0


# ---- _enrich_routes: the enrichment tick itself -----------------------------------

def _seed_aircraft(c, hex="a1b2c3", flight="ANZ423", lat=-41.3, lon=174.8):
    c.aircraft_adapter.record_sighting({
        "hex": hex, "flight": flight, "r": "ZK-TST", "t": "B738",
        "lat": lat, "lon": lon, "alt_baro": 5000, "gs": 200.0, "track": 0.0,
        "baro_rate": 0, "nav_altitude_mcp": None, "squawk": "2000",
    })


class _FakeResponse:
    def __init__(self, status, json_body=None):
        self.status = status
        self._json_body = json_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.post_calls = 0
        self.last_json = None

    def post(self, url, json=None, timeout=None):
        self.post_calls += 1
        self.last_json = json
        return self._response


@pytest.mark.asyncio
async def test_enrich_routes_does_nothing_with_no_known_callsigns():
    c = make_collector()
    session = _FakeSession(_FakeResponse(200, []))
    await c._enrich_routes(session)
    assert session.post_calls == 0


@pytest.mark.asyncio
async def test_enrich_routes_does_nothing_when_every_candidate_is_already_fresh():
    c = make_collector()
    _seed_aircraft(c)
    c.flight_route_adapter.record_routes({"ANZ423": {"stops": [], "plausible": True}})

    session = _FakeSession(_FakeResponse(200, []))
    await c._enrich_routes(session)
    assert session.post_calls == 0


@pytest.mark.asyncio
async def test_enrich_routes_fetches_and_stores_a_route_for_a_stale_callsign():
    c = make_collector()
    _seed_aircraft(c)

    body = [
        {
            "callsign": "ANZ423",
            "airport_codes": "WGN-AKL",
            "_airports": [
                {"icao": "NZWN", "iata": "WGN", "name": "Wellington"},
                {"icao": "NZAA", "iata": "AKL", "name": "Auckland"},
            ],
            "plausible": True,
        }
    ]
    session = _FakeSession(_FakeResponse(200, body))
    await c._enrich_routes(session)

    assert session.post_calls == 1
    assert c.flight_route_adapter.filter_stale(["ANZ423"], backstop_days=7) == []


@pytest.mark.asyncio
async def test_enrich_routes_stores_nothing_and_does_not_raise_on_a_failed_batch():
    """fetch_routes() returning None (whole-batch failure) must not crash the tick or
    get misrecorded as a confirmed no-match."""
    c = make_collector()
    _seed_aircraft(c)

    session = _FakeSession(_FakeResponse(429))
    await c._enrich_routes(session)  # must not raise
    # Still stale -- nothing was recorded for a failed batch.
    assert c.flight_route_adapter.filter_stale(["ANZ423"], backstop_days=7) == ["ANZ423"]


@pytest.mark.asyncio
async def test_enrich_routes_respects_the_configured_batch_size():
    c = make_collector({"route_batch_size": 1})
    _seed_aircraft(c, hex="hex1", flight="AAA111")
    _seed_aircraft(c, hex="hex2", flight="BBB222")

    session = _FakeSession(_FakeResponse(200, []))
    await c._enrich_routes(session)

    assert session.post_calls == 1
    assert len(session.last_json["planes"]) == 1
