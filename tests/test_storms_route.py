#!/usr/bin/env python3
"""Route-level test for GET /api/storms/geojson (architecture review candidate
"Give routers the seam the Fakes are waiting for"). Only proves the override
takes effect and the response contract holds -- constructing a realistic storm
cone/track fixture is its own, unrelated exercise already covered by
tests/test_storm_adapter.py against the Fake directly."""
from atmos_gl.db.storm_adapter import FakeStormAdapter
from atmos_gl.routes.storms import get_storm_adapter
from atmos_gl.api import app


def test_storms_geojson_uses_the_overridden_fake(client):
    fake = FakeStormAdapter()
    app.dependency_overrides[get_storm_adapter] = lambda: fake

    resp = client.get("/api/storms/geojson")

    # If the override didn't take effect, this would hit the real StormAdapter and
    # fail on a DB connection error instead of returning a clean empty collection.
    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}
