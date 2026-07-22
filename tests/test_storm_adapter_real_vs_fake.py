#!/usr/bin/env python3
"""Guard against StormAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeStormAdapter hand-reimplements
StormAdapter's track-line reconstruction in Python independently -- the SQL groups
storm_track rows by sid, orders each line by dt via aggregate_order_by, and only emits
a line when a category has more than one point (HAVING count(geom) > 1); the Fake
mirrors this with its own sort + len() check. Scoped to that delicate part -- ordering,
the >1-point threshold, and a CURRENT point appearing in both TRACK_PAST and
TRACK_FORECAST -- since if the SQL and the Fake's reimplementation ever diverge,
nothing else would catch it. tests/test_storm_adapter.py exercises only the Fake.
"""
import contextlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.storm_adapter import StormAdapter, FakeStormAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return StormAdapter(), patch("atmos_gl.db.storm_adapter.Session", TestSession)
    return FakeStormAdapter(), contextlib.nullcontext()


def _pt(record_type, lat, lon, hour, wind_kt=None, pressure_hpa=None, category=None):
    return {
        "TYPE": record_type,
        "TIME": datetime(2026, 1, 1, hour, tzinfo=timezone.utc),
        "TAU": 0,
        "LAT": lat,
        "LON": lon,
        "WIND_KT": wind_kt,
        "PRESSURE_HPA": pressure_hpa,
        "CATEGORY": category,
    }


def _track_features(adapter, sid):
    import json

    data = json.loads(adapter.get_storms_as_geojson())
    return [f for f in data["features"] if f["properties"].get("sid") == sid]


def _line(features, feature_type):
    matches = [f for f in features if f["properties"]["feature_type"] == feature_type]
    assert len(matches) <= 1, f"expected at most one {feature_type} feature, got {len(matches)}"
    return matches[0]["geometry"]["coordinates"] if matches else None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_track_line_orders_points_by_time_not_insertion_order(kind, real_db):
    sid = f"AL01{kind[:1].upper()}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        # Inserted out of chronological order.
        adapter.update_storm(
            sid, "Test",
            cone_vertices=[],
            track_points=[
                _pt("PAST", 10.0, -80.0, hour=2),
                _pt("PAST", 8.0, -78.0, hour=0),
                _pt("CURRENT", 12.0, -82.0, hour=4),
            ],
        )
        features = _track_features(adapter, sid)

    coords = _line(features, "TRACK_PAST")
    assert coords == [[-78.0, 8.0], [-80.0, 10.0], [-82.0, 12.0]]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_single_point_category_produces_no_line(kind, real_db):
    """HAVING count(geom) > 1 in the SQL -- a lone CURRENT point with no PAST/FORECAST
    neighbors must not draw a degenerate one-point line, matching the Fake's
    `len(matching) > 1` guard."""
    sid = f"AL02{kind[:1].upper()}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_storm(
            sid, "Test", cone_vertices=[], track_points=[_pt("CURRENT", 10.0, -80.0, hour=0)]
        )
        features = _track_features(adapter, sid)

    assert _line(features, "TRACK_PAST") is None
    assert _line(features, "TRACK_FORECAST") is None
    points = [f for f in features if f["properties"]["feature_type"] == "POINT"]
    assert len(points) == 1


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_current_point_is_shared_endpoint_of_both_lines(kind, real_db):
    """TRACK_PAST = PAST+CURRENT, TRACK_FORECAST = CURRENT+FORECAST -- the CURRENT
    point belongs to both categories and must appear as an endpoint of each line."""
    sid = f"AL03{kind[:1].upper()}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_storm(
            sid, "Test", cone_vertices=[],
            track_points=[
                _pt("PAST", 8.0, -78.0, hour=0),
                _pt("CURRENT", 10.0, -80.0, hour=2),
                _pt("FORECAST", 12.0, -82.0, hour=4),
            ],
        )
        features = _track_features(adapter, sid)

    past = _line(features, "TRACK_PAST")
    forecast = _line(features, "TRACK_FORECAST")
    current_point = [-80.0, 10.0]
    assert past[-1] == current_point
    assert forecast[0] == current_point


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_point_feature_carries_wind_pressure_and_category(kind, real_db):
    """The SQL's jsonb_build_object and the Fake's dict literal must expose
    wind_kt/pressure_hpa/category identically -- these are new columns added
    alongside the pre-existing POINT properties, an easy spot for Real/Fake drift."""
    sid = f"AL04{kind[:1].upper()}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_storm(
            sid, "Test", cone_vertices=[],
            track_points=[_pt("CURRENT", 10.0, -80.0, hour=0, wind_kt=85, pressure_hpa=965, category="HU")],
        )
        features = _track_features(adapter, sid)

    point = [f for f in features if f["properties"]["feature_type"] == "POINT"][0]
    assert point["properties"]["wind_kt"] == 85
    assert point["properties"]["pressure_hpa"] == 965
    assert point["properties"]["category"] == "HU"
