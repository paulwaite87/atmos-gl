#!/usr/bin/env python3
"""Route-level test for GET /api/quakes/geojson (architecture review candidate "Give
routers the seam the Fakes are waiting for"). QuakeAdapter is now injected via
Depends(get_quake_adapter), so a test can override it with the existing
FakeQuakeAdapter and exercise the real route end-to-end -- previously only the
adapter's own logic was tested (tests/test_quake_adapter.py), never this HTTP layer.
"""
from datetime import datetime, timezone

from atmos_gl.db.quake_adapter import FakeQuakeAdapter
from atmos_gl.routes.quakes import get_quake_adapter
from atmos_gl.api import app


def test_quakes_geojson_reflects_the_overridden_fake(client):
    fake = FakeQuakeAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.update_quake("q1", 5.5, 10.0, "Somewhere", now, -40.0, 175.0)
    app.dependency_overrides[get_quake_adapter] = lambda: fake

    resp = client.get("/api/quakes/geojson")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["id"] == "q1"


def test_quakes_geojson_passes_query_params_through_to_the_adapter(client):
    fake = FakeQuakeAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.update_quake("big", 5.0, 10.0, "A", now, -40.0, 175.0)
    fake.update_quake("small", 2.0, 10.0, "B", now, -40.0, 175.0)
    app.dependency_overrides[get_quake_adapter] = lambda: fake

    resp = client.get("/api/quakes/geojson", params={"min_mag": 3.5})

    ids = {f["properties"]["id"] for f in resp.json()["features"]}
    assert ids == {"big"}
