#!/usr/bin/env python3
"""Route-level test for GET /api/ships/geojson and GET /api/ships/{mmsi}/track
(architecture review candidate "Give routers the seam the Fakes are waiting for").
Only proves the override takes effect and the response contract holds --
constructing realistic AIS static/position fixtures, and exercising get_ship_track's
own ordering/limit/missing-mmsi behaviour, is already covered by
tests/test_ship_adapter.py against the Fake directly."""
from datetime import datetime, timezone

from atmos_gl.db.ship_adapter import FakeShipAdapter
from atmos_gl.routes.shipping import get_ship_adapter
from atmos_gl.api import app


def test_ships_geojson_uses_the_overridden_fake(client):
    fake = FakeShipAdapter()
    app.dependency_overrides[get_ship_adapter] = lambda: fake

    resp = client.get("/api/ships/geojson")

    # If the override didn't take effect, this would hit the real ShipAdapter and
    # fail on a DB connection error instead of returning a clean empty collection.
    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}


def test_ship_track_uses_the_overridden_fake(client):
    fake = FakeShipAdapter()
    fake._positions.append(
        {"mmsi": "123", "lat": 1.0, "lon": 2.0, "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    app.dependency_overrides[get_ship_adapter] = lambda: fake

    resp = client.get("/api/ships/123/track")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "data": [{"lat": 1.0, "lon": 2.0}]}


def test_ship_track_rejects_a_limit_outside_the_slider_range(client):
    app.dependency_overrides[get_ship_adapter] = lambda: FakeShipAdapter()

    assert client.get("/api/ships/123/track", params={"limit": 4}).status_code == 422
    assert client.get("/api/ships/123/track", params={"limit": 101}).status_code == 422
