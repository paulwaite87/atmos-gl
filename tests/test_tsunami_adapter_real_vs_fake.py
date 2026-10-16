#!/usr/bin/env python3
"""Guard against TsunamiAdapter Real/Fake drift (the pattern test_gdacs_adapter_real_vs_fake.py
follows): the same contract scenarios run against the SQLAlchemy adapter (a throwaway
postgis container migrated with `alembic upgrade head`) and FakeTsunamiAdapter, which
reimplements the SQL behaviour in Python. Everything is asserted through the adapter's
public methods, never by querying tables.
"""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from terrascope.db.tsunami_adapter import FakeTsunamiAdapter, TsunamiAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return TsunamiAdapter(), patch(
            "terrascope.db.tsunami_adapter.Session", TestSession
        )
    return FakeTsunamiAdapter(), contextlib.nullcontext()


def make_event(**over):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    event = {
        "centre": "PHEB",
        "event_id": "26289000",
        "category": "Information",
        "bulletin_number": 1,
        "location_name": "NEAR THE EAST COAST OF KAMCHATKA RUSSIA",
        "magnitude": 7.0,
        "magnitude_type": "Mwp",
        "depth_km": 148.0,
        "origin_time": now - timedelta(minutes=10),
        "expires": now + timedelta(hours=1),
        "bulletin_url": "http://www.tsunami.gov/events/PHEB/x/WEGM42.txt",
        "note": "A tsunami is not expected.",
        "lat": 51.86,
        "lon": 157.428,
        "zones": {},
    }
    event.update(over)
    return event


def points(adapter, **kwargs):
    return json.loads(adapter.get_events_as_geojson(**kwargs))["features"]


def served_ids(adapter, among, **kwargs):
    """Event ids served, limited to `among` -- the real database is shared across tests,
    so rows other tests created must not leak into this one's assertions."""
    return {f["properties"]["event_id"] for f in points(adapter, **kwargs)} & set(among)


def square(x, y):
    return {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1], [x, y]]],
    }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_a_later_bulletin_updates_the_event_in_place(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event(event_id="t1", category="Advisory"))
        adapter.upsert_event(
            make_event(event_id="t1", category="Warning", bulletin_number=2)
        )
        feats = [f for f in points(adapter) if f["properties"]["event_id"] == "t1"]

    assert len(feats) == 1
    props = feats[0]["properties"]
    assert (props["category"], props["bulletin_number"]) == ("Warning", 2)
    assert props["centre"] == "PHEB"
    assert props["magnitude"] == 7.0
    assert props["depth_km"] == 148.0
    assert props["note"] == "A tsunami is not expected."
    assert props["origin_time"].endswith("Z")
    assert feats[0]["geometry"] == {"type": "Point", "coordinates": [157.428, 51.86]}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_expired_event_shows_only_within_the_grace_period(kind, real_db):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event(event_id="g_active"))
        adapter.upsert_event(
            make_event(event_id="g_recent", expires=now - timedelta(hours=12))
        )
        adapter.upsert_event(
            make_event(event_id="g_old", expires=now - timedelta(days=3))
        )
        ids = ["g_active", "g_recent", "g_old"]
        default = served_ids(adapter, ids)
        none = served_ids(adapter, ids, grace_days=0)
        wide = served_ids(adapter, ids, grace_days=5)

    assert default == {"g_active", "g_recent"}
    assert none == {"g_active"}
    assert wide == {"g_active", "g_recent", "g_old"}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_events_footprint_is_its_cached_zones_with_their_categories(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_zone("TSZ001", square(10, 10))
        adapter.save_zone("TSZ002", square(20, 20))
        adapter.upsert_event(
            make_event(
                event_id="f1",
                zones={"TSZ001": "Warning", "TSZ002": "Advisory", "TSZ999": "Watch"},
            )
        )
        known = adapter.known_zone_codes()
        footprint = json.loads(adapter.get_footprint_as_geojson("PHEB", "f1"))

    assert {"TSZ001", "TSZ002"} <= known
    zones = {f["properties"]["zone_code"]: f for f in footprint["features"]}
    # TSZ999 was never cached, so it has no geometry to show.
    assert set(zones) == {"TSZ001", "TSZ002"}
    assert zones["TSZ001"]["properties"]["category"] == "Warning"
    assert zones["TSZ002"]["properties"]["category"] == "Advisory"
    assert zones["TSZ001"]["geometry"]["type"] == "Polygon"
    assert footprint["type"] == "FeatureCollection"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_a_later_bulletin_replaces_the_events_zone_list(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_zone("TSZ011", square(10, 10))
        adapter.save_zone("TSZ012", square(20, 20))
        adapter.upsert_event(make_event(event_id="r1", zones={"TSZ011": "Watch"}))
        adapter.upsert_event(make_event(event_id="r1", zones={"TSZ012": "Warning"}))
        footprint = json.loads(adapter.get_footprint_as_geojson("PHEB", "r1"))

    assert [f["properties"]["zone_code"] for f in footprint["features"]] == ["TSZ012"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_unknown_event_has_an_empty_footprint(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        footprint = json.loads(adapter.get_footprint_as_geojson("PHEB", "nope"))

    assert footprint == {"type": "FeatureCollection", "features": []}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_pruning_deletes_old_events_but_keeps_cached_zones(kind, real_db):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_zone("TSZ021", square(10, 10))
        adapter.upsert_event(make_event(event_id="p_fresh"))
        adapter.upsert_event(
            make_event(
                event_id="p_stale",
                expires=now - timedelta(days=30),
                zones={"TSZ021": "Warning"},
            )
        )
        deleted = adapter.prune_expired(7)
        ids = ["p_fresh", "p_stale"]
        remaining = served_ids(adapter, ids, grace_days=1000)
        stale_footprint = json.loads(
            adapter.get_footprint_as_geojson("PHEB", "p_stale")
        )
        zone_kept = "TSZ021" in adapter.known_zone_codes()

    assert deleted >= 1
    assert remaining == {"p_fresh"}
    assert stale_footprint["features"] == []
    assert zone_kept
