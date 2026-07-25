#!/usr/bin/env python3
"""Tests for lib/flight_radar.py -- the pure geometry helpers behind Flight Radar's
data acquisition (issue #203/#215). No real network in any of these -- fetch_aircraft_near
is exercised against fake aiohttp-shaped sessions.

RegionManager and viewport_to_region_keys (the WebSocket-era subscription lifecycle and
viewport-to-hot/gentle-key mapping, docs/adr/0009) were removed once AircraftCollector
replaced the WS route as adsb.lol's sole consumer -- see docs/adr/0010 and
tests/test_global_sample_scheduler.py for their replacement, GlobalSampleScheduler."""
import pytest

from atmos_gl.lib.flight_radar import circle_for_region_key, fetch_aircraft_near


class _FakeResponse:
    """Mimics aiohttp's response context manager: `session.get(...)` returns this
    directly (not a coroutine), and `async with ... as resp` drives it."""

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
        self.last_url = None

    def get(self, url, timeout=None):
        self.last_url = url
        return self._response


class _RaisingSession:
    def get(self, url, timeout=None):
        raise RuntimeError("connection failed")


def test_circle_for_region_key_centers_on_the_cell_center():
    lat, lon, _radius = circle_for_region_key((0, 0), grid_deg=5.0)
    assert lon == pytest.approx(2.5)
    assert lat == pytest.approx(2.5)


def test_circle_for_region_key_handles_negative_cells():
    lat, lon, _radius = circle_for_region_key((-1, -1), grid_deg=5.0)
    assert lon == pytest.approx(-2.5)
    assert lat == pytest.approx(-2.5)


def test_circle_for_region_key_uses_the_configured_radius():
    _lat, _lon, radius = circle_for_region_key((0, 0), grid_deg=5.0, radius_nm=123.0)
    assert radius == 123.0


# ---- fetch_aircraft_near: success vs. confirmed-empty vs. failed -----------------
# adsb.lol's free tier 429s far more readily than a naively-assumed cadence would
# expect -- a rejected/failed request must come back as None, distinct from a real []
# (a request that succeeded and genuinely found no aircraft in range).

@pytest.mark.asyncio
async def test_fetch_aircraft_near_returns_the_ac_list_on_success():
    session = _FakeSession(_FakeResponse(200, {"ac": [{"hex": "a1"}]}))
    result = await fetch_aircraft_near(session, 0.0, 0.0, 200.0)
    assert result == [{"hex": "a1"}]


@pytest.mark.asyncio
async def test_fetch_aircraft_near_returns_an_empty_list_when_ac_key_is_absent():
    """A 200 with no aircraft in range is a real, confirmed-empty result -- unlike a
    rejected request, it's fine to report this as []."""
    session = _FakeSession(_FakeResponse(200, {}))
    result = await fetch_aircraft_near(session, 0.0, 0.0, 200.0)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_aircraft_near_returns_none_not_empty_on_a_non_200_status():
    session = _FakeSession(_FakeResponse(429))
    result = await fetch_aircraft_near(session, 0.0, 0.0, 200.0)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_aircraft_near_returns_none_on_a_raised_exception():
    result = await fetch_aircraft_near(_RaisingSession(), 0.0, 0.0, 200.0)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_aircraft_near_uses_the_default_base_url_when_not_overridden():
    session = _FakeSession(_FakeResponse(200, {"ac": []}))
    await fetch_aircraft_near(session, 0.0, 0.0, 200.0)
    assert session.last_url.startswith("https://api.adsb.lol/v2/")


@pytest.mark.asyncio
async def test_fetch_aircraft_near_honors_a_configured_base_url():
    """AircraftCollector passes flightradar_collector's configured
    data_collector.datasources.flightradar value here rather than always using the
    hardcoded ADSB_LOL_BASE default -- the same maintainable-datasources-list
    convention every other collector follows."""
    session = _FakeSession(_FakeResponse(200, {"ac": []}))
    await fetch_aircraft_near(session, 0.0, 0.0, 200.0, base_url="https://my-mirror.example/v2")
    assert session.last_url.startswith("https://my-mirror.example/v2/")
