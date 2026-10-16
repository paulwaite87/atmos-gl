#!/usr/bin/env python3
"""Route-level tests for the Tsunami layer's display window, using FakeTsunamiAdapter via
app.dependency_overrides (see test_gdacs_route.py for the pattern)."""

from datetime import datetime, timedelta, timezone

from terrascope.api import app
from terrascope.db.tsunami_adapter import FakeTsunamiAdapter
from terrascope.routes.tsunami import get_tsunami_adapter


def event(event_id, *, expires_in_days):
    expires = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    return {
        "centre": "PHEB",
        "event_id": event_id,
        "category": "Warning",
        "bulletin_number": 1,
        "location_name": "Somewhere",
        "magnitude": 8.0,
        "magnitude_type": "Mww",
        "depth_km": 10.0,
        "origin_time": expires - timedelta(hours=1),
        "expires": expires,
        "bulletin_url": "https://www.tsunami.gov/x",
        "note": None,
        "lat": 1.0,
        "lon": 2.0,
        "zones": {},
    }


def served_ids(client, **params):
    resp = client.get("/api/tsunami/geojson", params=params)
    assert resp.status_code == 200
    return {f["properties"]["event_id"] for f in resp.json()["features"]}


def test_an_event_stays_visible_for_the_grace_period_after_its_bulletin_expires(client):
    adapter = FakeTsunamiAdapter()
    adapter.upsert_event(event("active", expires_in_days=0.5))
    adapter.upsert_event(event("just_expired", expires_in_days=-0.5))
    adapter.upsert_event(event("long_expired", expires_in_days=-3))
    app.dependency_overrides[get_tsunami_adapter] = lambda: adapter

    assert served_ids(client) == {"active", "just_expired"}
    assert served_ids(client, grace_days=0) == {"active"}
    assert served_ids(client, grace_days=5) == {
        "active",
        "just_expired",
        "long_expired",
    }
