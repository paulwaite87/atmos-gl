#!/usr/bin/env python3
"""Route-level tests for GET /api/forecast_state (architecture review candidate "Give
routers the seam the Fakes are waiting for")."""
from atmos_gl.db.field_catalog_adapter import FakeFieldCatalogAdapter
from atmos_gl.routes.config import get_field_catalog_adapter
from atmos_gl.api import app


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
