#!/usr/bin/env python3
"""Route-level test for GET /api/markers/geojson (architecture review candidate
"Give routers the seam the Fakes are waiting for")."""
from worldmap.db.marker_adapter import FakeMarkerAdapter
from worldmap.routes.markers import get_marker_adapter
from worldmap.api import app


def test_markers_geojson_reflects_the_overridden_fake(client):
    fake = FakeMarkerAdapter()
    fake.upsert_markers([
        {
            "id": "m1",
            "name": "Wellington",
            "kind": "city",
            "country": "NZ",
            "priority": 1,
            "pop": 200000,
            "capital": True,
            "color": "White",
            "timezone": "Pacific/Auckland",
            "lat": -41.28,
            "lon": 174.77,
        }
    ])
    app.dependency_overrides[get_marker_adapter] = lambda: fake

    resp = client.get("/api/markers/geojson")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 1
