#!/usr/bin/env python3
"""Route-level tests for GET /api/forecast_state and GET /api/regions (architecture
review candidate "Give routers the seam the Fakes are waiting for")."""
from worldmap.db.field_catalog_adapter import FakeFieldCatalogAdapter
from worldmap.db.region_adapter import FakeRegionAdapter
from worldmap.routes.config import get_field_catalog_adapter, get_region_adapter
from worldmap.api import app


def _seed_region(adapter, label, lon_min, lat_min, lon_max, lat_max):
    adapter._regions[label] = {
        "lon_min": lon_min,
        "lat_min": lat_min,
        "lon_max": lon_max,
        "lat_max": lat_max,
    }


def test_forecast_state_reflects_the_overridden_fake(client):
    fake = FakeFieldCatalogAdapter()
    fake.upsert_field_catalog("2026-06-13", "18", 0, "isobars", 721, 1440)
    fake.upsert_field_catalog("2026-06-13", "18", 3, "isobars", 721, 1440)
    app.dependency_overrides[get_field_catalog_adapter] = lambda: fake

    resp = client.get("/api/forecast_state")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["primary"] == "gfs"
    assert body["data"]["sources"]["gfs"]["hours"] == [0, 3]


def test_forecast_state_is_null_when_the_fake_has_no_data(client):
    fake = FakeFieldCatalogAdapter()
    app.dependency_overrides[get_field_catalog_adapter] = lambda: fake

    resp = client.get("/api/forecast_state")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "data": None}


def test_regions_reflects_the_overridden_fake(client):
    fake = FakeRegionAdapter()
    _seed_region(fake, "NZ", 165.0, -47.0, 179.0, -34.0)
    _seed_region(fake, "AU", 113.0, -44.0, 154.0, -10.0)
    app.dependency_overrides[get_region_adapter] = lambda: fake

    resp = client.get("/api/regions")

    assert resp.status_code == 200
    labels = [r["label"] for r in resp.json()["data"]]
    assert set(labels) == {"NZ", "AU"}
