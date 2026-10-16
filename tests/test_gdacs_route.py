#!/usr/bin/env python3
"""Route-level tests for the GDACS layer's HTTP parameters, using FakeGdacsAdapter via
app.dependency_overrides (see test_quakes_route.py for the pattern)."""
from datetime import datetime, timedelta, timezone

from terrascope.api import app
from terrascope.db.gdacs_adapter import FakeGdacsAdapter
from terrascope.routes.gdacs import get_gdacs_adapter


def event(event_id, *, is_current, ended_days_ago):
    ended = datetime.now(timezone.utc) - timedelta(days=ended_days_ago)
    return {
        "event_type": "FL",
        "event_id": event_id,
        "episode_id": 1,
        "name": "Flood",
        "alert_level": "Orange",
        "alert_score": 2.0,
        "severity": 1.0,
        "severity_text": "",
        "severity_unit": "",
        "country": "Nowhere",
        "iso3": "NOW",
        "from_date": (ended - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S"),
        "to_date": ended.strftime("%Y-%m-%dT%H:%M:%S"),
        "date_modified": ended.strftime("%Y-%m-%dT%H:%M:%S"),
        "is_current": is_current,
        "report_url": "https://www.gdacs.org/report.aspx",
        "lat": 1.0,
        "lon": 2.0,
    }


def served_ids(client, **params):
    resp = client.get("/api/gdacs/geojson", params=params)
    assert resp.status_code == 200
    return {f["properties"]["event_id"] for f in resp.json()["features"]}


def test_grace_days_query_widens_how_long_ended_events_stay(client):
    fake = FakeGdacsAdapter()
    fake.upsert_event(event(1, is_current=True, ended_days_ago=0))
    fake.upsert_event(event(2, is_current=False, ended_days_ago=10))
    app.dependency_overrides[get_gdacs_adapter] = lambda: fake

    assert served_ids(client) == {1}  # default grace is 7 days
    assert served_ids(client, grace_days=14) == {1, 2}


def test_footprint_route_serves_an_unknown_event_as_an_empty_collection(client):
    app.dependency_overrides[get_gdacs_adapter] = lambda: FakeGdacsAdapter()

    resp = client.get("/api/gdacs/FL/424242/footprint")

    assert resp.status_code == 200
    assert resp.json() == {"type": "FeatureCollection", "features": []}
