#!/usr/bin/env python3
"""Route-level tests for GET /api/lightning/geojson and POST /api/viewport
(architecture review candidate "Give routers the seam the Fakes are waiting for")."""
from datetime import datetime, timezone

from atmos_gl.db.lightning_adapter import FakeLightningAdapter
from atmos_gl.db.viewport_adapter import FakeViewportAdapter
from atmos_gl.routes.lightning import get_lightning_adapter, get_viewport_adapter
from atmos_gl.api import app


def test_lightning_geojson_reflects_the_overridden_fake(client):
    fake = FakeLightningAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.update_lightning_strike("s1", -40.0, 175.0, 90, now)
    app.dependency_overrides[get_lightning_adapter] = lambda: fake

    resp = client.get("/api/lightning/geojson")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 1


def test_post_viewport_stores_via_the_overridden_fake(client):
    fake = FakeViewportAdapter()
    app.dependency_overrides[get_viewport_adapter] = lambda: fake

    resp = client.post("/api/viewport", json={"lat": -41.3, "lon": 174.8, "zoom": 5.5})

    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    stored = fake.get_viewport()
    assert stored["lat"] == -41.3
    assert stored["lon"] == 174.8
    assert stored["zoom"] == 5.5


def test_post_viewport_zoom_is_optional(client):
    fake = FakeViewportAdapter()
    app.dependency_overrides[get_viewport_adapter] = lambda: fake

    resp = client.post("/api/viewport", json={"lat": 0.0, "lon": 0.0})

    assert resp.status_code == 200
    assert fake.get_viewport()["zoom"] is None
