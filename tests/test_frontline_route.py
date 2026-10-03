#!/usr/bin/env python3
"""GET /api/frontline/geojson with FakeFrontlineAdapter injected via Depends."""
from datetime import datetime, timezone

import pytest

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


def _box_fc(x1):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"status": "occupied"},
        "geometry": {"type": "Polygon", "coordinates": [[[37.0, 48.0], [x1, 48.0], [x1, 48.2], [37.0, 48.2], [37.0, 48.0]]]},
    }]}


def test_frontline_changes_compares_against_the_window_baseline(client):
    fake = FakeFrontlineAdapter()
    fake.save_snapshot(1, datetime(2026, 9, 1, tzinfo=timezone.utc), None, _box_fc(37.1))
    fake.save_snapshot(2, datetime(2026, 9, 24, tzinfo=timezone.utc), None, _box_fc(37.2))
    fake.save_snapshot(3, datetime(2026, 10, 1, tzinfo=timezone.utc), None, _box_fc(37.3))
    app.dependency_overrides[get_frontline_adapter] = lambda: fake

    week = client.get("/api/frontline/changes?days=7").json()
    month = client.get("/api/frontline/changes?days=30").json()

    assert week["comparison"]["from"]["id"] == 2
    assert month["comparison"]["from"]["id"] == 1
    assert month["comparison"]["totals_km2"]["russian_gain"] == pytest.approx(
        2 * week["comparison"]["totals_km2"]["russian_gain"], rel=0.02)


def test_frontline_changes_rejects_an_unoffered_window(client):
    app.dependency_overrides[get_frontline_adapter] = lambda: FakeFrontlineAdapter()
    assert client.get("/api/frontline/changes?days=5").status_code == 422


def test_frontline_changes_before_any_baseline_has_no_comparison(client):
    fake = FakeFrontlineAdapter()
    fake.save_snapshot(3, datetime(2026, 10, 1, tzinfo=timezone.utc), None, _box_fc(37.3))
    app.dependency_overrides[get_frontline_adapter] = lambda: fake
    assert client.get("/api/frontline/changes?days=7").json()["comparison"] is None
