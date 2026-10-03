#!/usr/bin/env python3
"""GET /api/frontline/geojson with FakeFrontlineAdapter injected via Depends."""
from datetime import datetime, timezone

from atmos_gl.api import app
from atmos_gl.db.frontline_adapter import FakeFrontlineAdapter
from atmos_gl.routes.frontline import get_frontline_adapter


def test_frontline_geojson_serves_the_latest_snapshot(client):
    fake = FakeFrontlineAdapter()
    fake.save_snapshot(42, datetime(2026, 10, 1, tzinfo=timezone.utc), "Update",
                       {"type": "FeatureCollection", "features": []})
    app.dependency_overrides[get_frontline_adapter] = lambda: fake

    resp = client.get("/api/frontline/geojson")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["snapshot"]["id"] == 42
