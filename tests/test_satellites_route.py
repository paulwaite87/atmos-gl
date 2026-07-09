#!/usr/bin/env python3
"""Route-level test for GET /api/satellites/geojson (architecture review candidate
"Give routers the seam the Fakes are waiting for"). Only proves the adapter override
takes effect -- the route's own orbital propagation (sgp4/skyfield) needs real OMM
element sets to produce non-empty output, which is unrelated to this candidate; with
no matching rows the route already short-circuits to an empty collection before any
propagation runs."""
from atmos_gl.db.satellite_adapter import FakeSatelliteAdapter
from atmos_gl.routes.satellites import get_satellite_adapter
from atmos_gl.api import app


def test_satellites_geojson_uses_the_overridden_fake(client):
    fake = FakeSatelliteAdapter()
    app.dependency_overrides[get_satellite_adapter] = lambda: fake

    resp = client.get("/api/satellites/geojson")

    # If the override didn't take effect, this would hit the real SatelliteAdapter and
    # fail on a DB connection error instead of returning a clean empty collection.
    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}
