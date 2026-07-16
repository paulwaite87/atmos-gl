#!/usr/bin/env python3
"""Route-level test for GET /api/fires/geojson, mirroring test_quakes_route.py.
FireAdapter is injected via Depends(get_fire_adapter), so a test can override it with
FakeFireAdapter and exercise the real route end-to-end.
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import numpy as np

from atmos_gl.db.fire_adapter import FakeFireAdapter
from atmos_gl.routes.fires import get_fire_adapter
from atmos_gl.api import app


def test_fires_geojson_reflects_the_overridden_fake(client):
    fake = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.upsert_fires([{
        "id": "f1", "lat": -40.0, "lon": 175.0, "brightness": 330.0, "frp": 10.0,
        "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now,
    }])
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
    fake.upsert_fires([
        {"id": "hi", "lat": -40.0, "lon": 175.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "high", "satellite": "N", "daynight": "D", "acq_time": now},
        {"id": "lo", "lat": -40.0, "lon": 175.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "low", "satellite": "N", "daynight": "D", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake

    resp = client.get("/api/fires/geojson", params={"min_confidence": "nominal"})

    ids = {f["properties"]["id"] for f in resp.json()["features"]}
    assert ids == {"hi"}


def test_fires_geojson_passes_max_frp_through_to_the_adapter(client):
    fake = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake.upsert_fires([
        {"id": "plausible", "lat": -40.0, "lon": 175.0, "brightness": 330.0, "frp": 800.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
        {"id": "flare", "lat": -40.0, "lon": 175.0, "brightness": 330.0, "frp": 12444.0,
         "confidence": "nominal", "satellite": "N", "daynight": "N", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake

    resp = client.get("/api/fires/geojson", params={"max_frp": 5000})

    ids = {f["properties"]["id"] for f in resp.json()["features"]}
    assert ids == {"plausible"}


def _fake_fire_weather_store():
    """A 2x2 grid: (lat=0,lon=0) -> row=1,col=0 -> risk 80; (lat=10,lon=10) ->
    row=0,col=1 -> risk 5. Mirrors _sample_nearest's row/col arithmetic in
    routes/fires.py."""
    field = {
        "lat": np.array([10.0, 0.0]),
        "lon": np.array([0.0, 10.0]),
        "values": np.array([[0.0, 5.0], [80.0, 0.0]]),
    }
    store = MagicMock()
    store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": "2026-01-01", "run_id": "00", "hours": [0],
    }
    store.get_field.return_value = field
    return store


def test_fires_geojson_min_risk_filters_out_low_risk_detections(client):
    fake_adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake_adapter.upsert_fires([
        {"id": "risky", "lat": 0.0, "lon": 0.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
        {"id": "safe", "lat": 10.0, "lon": 10.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake_adapter

    with patch("atmos_gl.routes.fires.load_config") as mock_load_config, patch(
        "atmos_gl.routes.fires.fieldstore.get_store", return_value=_fake_fire_weather_store()
    ):
        mock_load_config.return_value.get_setting.return_value = "."
        resp = client.get("/api/fires/geojson", params={"min_risk": 50})

    ids = {f["properties"]["id"] for f in resp.json()["features"]}
    assert ids == {"risky"}


def test_fires_geojson_attaches_fire_risk_to_every_detection(client):
    """fire_risk is attached unconditionally (not just when min_risk filtering is
    active) -- the frontend popup shows it for every detection."""
    fake_adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake_adapter.upsert_fires([
        {"id": "f1", "lat": 0.0, "lon": 0.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake_adapter

    with patch("atmos_gl.routes.fires.load_config") as mock_load_config, patch(
        "atmos_gl.routes.fires.fieldstore.get_store", return_value=_fake_fire_weather_store()
    ):
        mock_load_config.return_value.get_setting.return_value = "."
        resp = client.get("/api/fires/geojson")

    body = resp.json()
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["fire_risk"] == 80.0


def test_fires_geojson_min_risk_zero_does_not_filter_but_still_attaches_risk(client):
    """min_risk=0 (the default) must not drop anything, but fire_risk is still
    attached -- filtering is opt-in, attaching for the popup is not."""
    fake_adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake_adapter.upsert_fires([
        {"id": "risky", "lat": 0.0, "lon": 0.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
        {"id": "safe", "lat": 10.0, "lon": 10.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake_adapter

    with patch("atmos_gl.routes.fires.load_config") as mock_load_config, patch(
        "atmos_gl.routes.fires.fieldstore.get_store", return_value=_fake_fire_weather_store()
    ):
        mock_load_config.return_value.get_setting.return_value = "."
        resp = client.get("/api/fires/geojson")

    body = resp.json()
    risk_by_id = {f["properties"]["id"]: f["properties"]["fire_risk"] for f in body["features"]}
    assert risk_by_id == {"risky": 80.0, "safe": 5.0}


def test_fires_geojson_degrades_gracefully_when_fieldstore_lookup_fails(client):
    """A fieldstore/DB hiccup while attaching fire_risk must not break the whole
    endpoint -- detections still come back, just without fire_risk."""
    fake_adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc).isoformat()
    fake_adapter.upsert_fires([
        {"id": "f1", "lat": 0.0, "lon": 0.0, "brightness": 330.0, "frp": 10.0,
         "confidence": "nominal", "satellite": "N", "daynight": "D", "acq_time": now},
    ])
    app.dependency_overrides[get_fire_adapter] = lambda: fake_adapter

    with patch("atmos_gl.routes.fires.load_config", side_effect=RuntimeError("boom")):
        resp = client.get("/api/fires/geojson")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 1
    assert "fire_risk" not in body["features"][0]["properties"]
