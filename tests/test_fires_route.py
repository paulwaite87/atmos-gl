#!/usr/bin/env python3
"""Route-level test for GET /api/fires/geojson, mirroring test_quakes_route.py.
FireAdapter is injected via Depends(get_fire_adapter), so a test can override it with
FakeFireAdapter and exercise the real route end-to-end.
"""
from datetime import datetime, timezone

from atmos_gl.db.fire_adapter import FakeFireAdapter
from atmos_gl.routes.fires import get_fire_adapter
from atmos_gl.api import app


def test_fires_geojson_reflects_the_overridden_fake(client):
    fake = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.update_fire("f1", -40.0, 175.0, 330.0, 10.0, "nominal", "N", "D", now)
    app.dependency_overrides[get_fire_adapter] = lambda: fake

    resp = client.get("/api/fires/geojson")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["id"] == "f1"


def test_fires_geojson_passes_query_params_through_to_the_adapter(client):
    fake = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.update_fire("hi", -40.0, 175.0, 330.0, 10.0, "high", "N", "D", now)
    fake.update_fire("lo", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", now)
    app.dependency_overrides[get_fire_adapter] = lambda: fake

    resp = client.get("/api/fires/geojson", params={"min_confidence": "nominal"})

    ids = {f["properties"]["id"] for f in resp.json()["features"]}
    assert ids == {"hi"}
