#!/usr/bin/env python3
"""Route-level test for POST /api/request_backfill (architecture review candidate
"Give routers the seam the Fakes are waiting for")."""
from atmos_gl.db.field_catalog_adapter import FakeFieldCatalogAdapter
from atmos_gl.routes.backfill import get_field_catalog_adapter
from atmos_gl.api import app


def test_request_backfill_enqueues_via_the_overridden_fake(client):
    fake = FakeFieldCatalogAdapter()
    app.dependency_overrides[get_field_catalog_adapter] = lambda: fake

    resp = client.post(
        "/api/request_backfill",
        json={"product": "wind", "date": "20260613", "run": "06", "hour": 12},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    # Confirms the request actually reached the injected fake, not a real adapter.
    claimed = fake.claim_backfill_requests()
    assert len(claimed) == 1
    assert claimed[0]["product"] == "wind"


def test_request_backfill_rejects_an_unknown_product(client):
    fake = FakeFieldCatalogAdapter()
    app.dependency_overrides[get_field_catalog_adapter] = lambda: fake

    resp = client.post(
        "/api/request_backfill",
        json={"product": "not-a-real-product", "date": "20260613", "run": "06", "hour": 12},
    )

    assert resp.status_code == 400
    assert fake.claim_backfill_requests() == []


def test_request_backfill_rejects_an_invalid_run(client):
    fake = FakeFieldCatalogAdapter()
    app.dependency_overrides[get_field_catalog_adapter] = lambda: fake

    resp = client.post(
        "/api/request_backfill",
        json={"product": "wind", "date": "20260613", "run": "99", "hour": 12},
    )

    assert resp.status_code == 400
