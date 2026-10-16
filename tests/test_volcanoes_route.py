#!/usr/bin/env python3
"""Route-level test for GET /api/volcanoes/geojson (issue #253)."""
from terrascope.db.volcanic_activity_adapter import FakeVolcanicActivityAdapter
from terrascope.routes.volcanoes import get_volcano_adapter
from terrascope.api import app


def test_volcanoes_geojson_reflects_the_overridden_fake(client):
    fake = FakeVolcanicActivityAdapter()
    fake.upsert_activity(
        "311120", "Great Sitkin", "United States", 52.08, -176.13,
        "Continuing Eruptive Activity", "Report text.", None, None, None,
    )
    app.dependency_overrides[get_volcano_adapter] = lambda: fake

    resp = client.get("/api/volcanoes/geojson")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["name"] == "Great Sitkin"


def test_volcanoes_geojson_takes_no_query_parameters(client):
    """The old vei_min/significant/codes filters are gone entirely -- the route
    returns everything the adapter has, unfiltered."""
    fake = FakeVolcanicActivityAdapter()
    fake.upsert_activity("1", "A", "Country", 0.0, 0.0, "New Unrest", None, None, None, None)
    fake.upsert_activity("2", "B", "Country", 1.0, 1.0, "Continuing Eruptive Activity", None, None, None, None)
    app.dependency_overrides[get_volcano_adapter] = lambda: fake

    resp = client.get("/api/volcanoes/geojson")

    names = {f["properties"]["name"] for f in resp.json()["features"]}
    assert names == {"A", "B"}
