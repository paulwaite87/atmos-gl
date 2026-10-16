#!/usr/bin/env python3
"""Guard against GdacsAdapter Real/Fake drift (the pattern test_quake_adapter_real_vs_fake.py
established): the same contract scenarios run against the SQLAlchemy adapter (a throwaway
postgis container migrated with `alembic upgrade head`) and FakeGdacsAdapter, which
reimplements the SQL behaviour in Python. Everything is asserted through the adapter's
public GeoJSON methods, never by querying tables.
"""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from terrascope.db.gdacs_adapter import FakeGdacsAdapter, GdacsAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return GdacsAdapter(), patch("terrascope.db.gdacs_adapter.Session", TestSession)
    return FakeGdacsAdapter(), contextlib.nullcontext()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def make_event(**over):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    event = {
        "event_type": "FL",
        "event_id": 1103888,
        "episode_id": 91,
        "name": "Flood in United States",
        "alert_level": "Green",
        "alert_score": 1.0,
        "severity": 0.0,
        "severity_text": "Magnitude 0",
        "severity_unit": "",
        "country": "United States",
        "iso3": "USA",
        "from_date": iso(now - timedelta(days=30)),
        "to_date": iso(now),
        "date_modified": iso(now),
        "is_current": True,
        "report_url": "https://www.gdacs.org/report.aspx?eventid=1103888&episodeid=91&eventtype=FL",
        "lat": 38.5,
        "lon": -97.25,
    }
    event.update(over)
    return event


def points(adapter, **kwargs):
    return json.loads(adapter.get_events_as_geojson(**kwargs))["features"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_upserting_the_same_event_updates_it_in_place(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(
            make_event(event_id=7001, episode_id=1, alert_level="Green")
        )
        adapter.upsert_event(
            make_event(
                event_id=7001, episode_id=2, alert_level="Orange", alert_score=2.0
            )
        )
        feats = [f for f in points(adapter) if f["properties"]["event_id"] == 7001]

    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["alert_level"] == "Orange"
    assert props["event_type"] == "FL"
    assert props["country"] == "United States"
    assert feats[0]["geometry"] == {"type": "Point", "coordinates": [-97.25, 38.5]}


def served_ids(adapter, among, **kwargs):
    """Event ids served, limited to `among` -- the real database is shared across tests,
    so rows other tests created must not leak into this one's assertions."""
    return {f["properties"]["event_id"] for f in points(adapter, **kwargs)} & set(among)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_current_events_always_show_and_ended_ones_only_within_the_grace_period(
    kind, real_db
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        # A flood GDACS still flags current, even though its last date is 40 days back.
        adapter.upsert_event(
            make_event(
                event_id=7101, is_current=True, to_date=iso(now - timedelta(days=40))
            )
        )
        adapter.upsert_event(
            make_event(
                event_id=7102, is_current=False, to_date=iso(now - timedelta(days=3))
            )
        )
        adapter.upsert_event(
            make_event(
                event_id=7103, is_current=False, to_date=iso(now - timedelta(days=10))
            )
        )
        mine = {7101, 7102, 7103}
        default_grace = served_ids(adapter, mine)
        wider_grace = served_ids(adapter, mine, grace_days=14)

    assert default_grace == {7101, 7102}
    assert wider_grace == {7101, 7102, 7103}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_dates_are_served_as_utc_iso_strings(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(
            make_event(
                event_id=7201,
                from_date="2026-10-01T06:30:00",
                to_date=iso(datetime.now(timezone.utc)),
            )
        )
        feat = next(f for f in points(adapter) if f["properties"]["event_id"] == 7201)

    assert feat["properties"]["from_date"] == "2026-10-01T06:30:00Z"


def rectangle(x0, y0, x1, y1):
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def dense_rectangle():
    """A (10,10)-(12,12) rectangle whose bottom edge carries 20 near-collinear midpoints
    (wobbling by 0.0005 degrees) -- the shape of detail GDACS flood outlines are full of.
    """
    bottom = [[10 + 0.1 * i, 10 + (0.0005 if i % 2 else 0.0)] for i in range(21)]
    return {"type": "Polygon", "coordinates": [bottom + [[12, 12], [10, 12], [10, 10]]]}


def vertex_count(geometry):
    return len(geometry["coordinates"][0])


def footprint(adapter, event_type, event_id):
    return json.loads(adapter.get_footprint_as_geojson(event_type, event_id))[
        "features"
    ]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_footprint_parts_are_served_per_class_and_replaced_wholesale(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event(event_id=7301))
        adapter.replace_footprint(
            "FL",
            7301,
            "2026-10-16T02:00:00",
            [
                {
                    "feature_class": "Poly_Affected",
                    "geometry": rectangle(10, 10, 12, 12),
                },
                {"feature_class": "Poly_Global", "geometry": rectangle(0, 0, 40, 40)},
            ],
        )
        first = footprint(adapter, "FL", 7301)
        adapter.replace_footprint(
            "FL",
            7301,
            "2026-10-17T02:00:00",
            [{"feature_class": "Poly_Affected", "geometry": rectangle(11, 11, 13, 13)}],
        )
        second = footprint(adapter, "FL", 7301)

    assert [f["properties"]["feature_class"] for f in first] == [
        "Poly_Affected",
        "Poly_Global",
    ]
    assert first[0]["properties"]["event_id"] == 7301
    assert first[0]["geometry"]["type"] == "Polygon"
    # The second fetch replaced everything: the old global polygon is gone.
    assert [f["properties"]["feature_class"] for f in second] == ["Poly_Affected"]
    assert second[0]["geometry"]["coordinates"][0][0] == [11.0, 11.0]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_footprint_geometry_is_simplified_but_keeps_its_extent(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event(event_id=7302))
        adapter.replace_footprint(
            "FL",
            7302,
            "2026-10-16T02:00:00",
            [{"feature_class": "Poly_Affected", "geometry": dense_rectangle()}],
        )
        geometry = footprint(adapter, "FL", 7302)[0]["geometry"]

    assert vertex_count(dense_rectangle()) == 24
    assert vertex_count(geometry) <= 6
    xs = [c[0] for c in geometry["coordinates"][0]]
    ys = [c[1] for c in geometry["coordinates"][0]]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx(
        (10, 10, 12, 12), abs=1e-3
    )


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_footprint_marker_reports_what_was_stored(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event(event_id=7303))
        before = adapter.footprint_marker("FL", 7303)
        adapter.replace_footprint(
            "FL",
            7303,
            "2026-10-16T02:08:27",
            [{"feature_class": "Poly_Affected", "geometry": rectangle(1, 1, 2, 2)}],
        )
        after = adapter.footprint_marker("FL", 7303)

    assert before is None
    assert after == "2026-10-16T02:08:27"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_event_with_no_stored_footprint_serves_an_empty_collection(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        body = json.loads(adapter.get_footprint_as_geojson("FL", 999999999))

    assert body == {"type": "FeatureCollection", "features": []}
