#!/usr/bin/env python3
"""Route-level tests for GET /api/flightradar/geojson and GET /api/flightradar/{hex}/track
(issue #215), mirroring tests/test_shipping_route.py's DI-override pattern. Only proves
the override takes effect, the response contract holds, and that a read request also
records viewer interest -- adapter-level upsert/read/prune behavior is already covered
by tests/test_aircraft_adapter.py against the Fake directly."""
from datetime import datetime, timezone

from atmos_gl.db.aircraft_adapter import FakeAircraftAdapter
from atmos_gl.routes.flightradar import get_aircraft_adapter
from atmos_gl.api import app


def _bbox_params(viewer_id="viewer-1"):
    return {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0, "viewer_id": viewer_id}


def test_flightradar_geojson_uses_the_overridden_fake(client):
    fake = FakeAircraftAdapter()
    app.dependency_overrides[get_aircraft_adapter] = lambda: fake

    resp = client.get("/api/flightradar/geojson", params=_bbox_params())

    # If the override didn't take effect, this would hit the real AircraftAdapter and
    # fail on a DB connection error instead of returning a clean empty collection.
    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}


def test_flightradar_geojson_records_viewer_interest_as_a_side_effect(client):
    fake = FakeAircraftAdapter()
    app.dependency_overrides[get_aircraft_adapter] = lambda: fake

    client.get("/api/flightradar/geojson", params=_bbox_params(viewer_id="viewer-42"))

    assert fake.get_active_interest(max_age_s=60.0) == [(0.0, 0.0, 1.0, 1.0)]


def test_flightradar_geojson_requires_all_bbox_params(client):
    app.dependency_overrides[get_aircraft_adapter] = lambda: FakeAircraftAdapter()

    resp = client.get("/api/flightradar/geojson", params={"west": 0.0, "viewer_id": "v"})

    assert resp.status_code == 422


def test_flightradar_track_uses_the_overridden_fake(client):
    fake = FakeAircraftAdapter()
    fake._tracks.append(
        {"hex": "a1b2c3", "lat": 1.0, "lon": 2.0, "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    app.dependency_overrides[get_aircraft_adapter] = lambda: fake

    resp = client.get("/api/flightradar/a1b2c3/track")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "data": [{"lat": 1.0, "lon": 2.0}]}


def test_flightradar_track_rejects_a_limit_outside_the_slider_range(client):
    app.dependency_overrides[get_aircraft_adapter] = lambda: FakeAircraftAdapter()

    assert client.get("/api/flightradar/a1b2c3/track", params={"limit": 4}).status_code == 422
    assert client.get("/api/flightradar/a1b2c3/track", params={"limit": 101}).status_code == 422
