#!/usr/bin/env python3
"""Route-level test for GET /api/ships/geojson (architecture review candidate "Give
routers the seam the Fakes are waiting for"). Only proves the override takes effect
and the response contract holds -- constructing realistic AIS static/position
fixtures is its own, unrelated exercise already covered by tests/test_ship_adapter.py
against the Fake directly."""
from worldmap.db.ship_adapter import FakeShipAdapter
from worldmap.routes.shipping import get_ship_adapter
from worldmap.api import app


def test_ships_geojson_uses_the_overridden_fake(client):
    fake = FakeShipAdapter()
    app.dependency_overrides[get_ship_adapter] = lambda: fake

    resp = client.get("/api/ships/geojson")

    # If the override didn't take effect, this would hit the real ShipAdapter and
    # fail on a DB connection error instead of returning a clean empty collection.
    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}
