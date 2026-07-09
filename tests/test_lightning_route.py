#!/usr/bin/env python3
"""Route-level test for GET /api/lightning/geojson (architecture review candidate
"Give routers the seam the Fakes are waiting for")."""
from datetime import datetime, timezone

from atmos_gl.db.lightning_adapter import FakeLightningAdapter
from atmos_gl.routes.lightning import get_lightning_adapter
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
