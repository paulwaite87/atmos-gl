#!/usr/bin/env python3
"""Real-DB test for get_aircraft_track()'s ground-truncation behavior -- the SQL
query now also selects on_ground and the Python loop truncates on it, which the
Fake-only tests in test_aircraft_adapter.py can't verify against a real Postgres
NULL/boolean round-trip."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.aircraft_adapter import AircraftAdapter


@pytest.fixture
def aircraft_adapter(real_db):
    TestSession = sessionmaker(bind=real_db)
    with patch("atmos_gl.db.aircraft_adapter.Session", TestSession):
        yield AircraftAdapter()


def _sighting(hex, lat, on_ground=False, **overrides):
    base = {
        "hex": hex, "flight": "GNDTRUNC1", "r": "ZK-TST", "t": "B738",
        "lat": lat, "lon": lat,
        "alt_baro": "ground" if on_ground else 5000,
        "gs": 0.0 if on_ground else 200.0, "track": 0.0,
        "baro_rate": 0, "nav_altitude_mcp": None, "squawk": "2000",
    }
    base.update(overrides)
    return base


def test_track_truncates_at_the_most_recent_ground_row(aircraft_adapter):
    hex_id = "gndtr01"
    now = datetime.now(timezone.utc)
    aircraft_adapter.record_sighting(_sighting(hex_id, 0.0, on_ground=True), now=now - timedelta(minutes=30))
    aircraft_adapter.record_sighting(_sighting(hex_id, 1.0, on_ground=True), now=now - timedelta(minutes=20))
    aircraft_adapter.record_sighting(_sighting(hex_id, 2.0), now=now - timedelta(minutes=10))
    aircraft_adapter.record_sighting(_sighting(hex_id, 3.0), now=now)

    track = aircraft_adapter.get_aircraft_track(hex_id, limit=100)
    assert [p["lat"] for p in track] == [3.0, 2.0, 1.0]


def test_track_returns_full_history_when_never_on_ground(aircraft_adapter):
    hex_id = "gndtr02"
    now = datetime.now(timezone.utc)
    for i, lat in enumerate([0.0, 1.0, 2.0]):
        aircraft_adapter.record_sighting(_sighting(hex_id, lat), now=now - timedelta(minutes=(2 - i) * 10))

    track = aircraft_adapter.get_aircraft_track(hex_id, limit=100)
    assert len(track) == 3
