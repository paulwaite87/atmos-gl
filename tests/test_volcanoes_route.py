#!/usr/bin/env python3
"""Route-level test for GET /api/volcanoes/geojson (architecture review candidate
"Give routers the seam the Fakes are waiting for")."""
from worldmap.db.volcano_adapter import FakeVolcanoAdapter
from worldmap.routes.volcanoes import get_volcano_adapter
from worldmap.api import app


def test_volcanoes_geojson_reflects_the_overridden_fake(client):
    fake = FakeVolcanoAdapter()
    fake.update_volcano("v1", "Ruapehu", -39.28, 175.57, 4, True, "D1")
    app.dependency_overrides[get_volcano_adapter] = lambda: fake

    resp = client.get("/api/volcanoes/geojson", params={"codes": "D1"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["name"] == "Ruapehu"


def test_volcanoes_geojson_filters_by_vei_min(client):
    fake = FakeVolcanoAdapter()
    fake.update_volcano("small", "Small One", 0.0, 0.0, 1, True, "D1")
    fake.update_volcano("big", "Big One", 0.0, 0.0, 5, True, "D1")
    app.dependency_overrides[get_volcano_adapter] = lambda: fake

    resp = client.get("/api/volcanoes/geojson", params={"codes": "D1", "vei_min": 4})

    names = {f["properties"]["name"] for f in resp.json()["features"]}
    assert names == {"Big One"}
