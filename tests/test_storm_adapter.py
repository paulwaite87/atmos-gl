import json
from datetime import datetime, timedelta, timezone

from atmos_gl.db.storm_adapter import FakeStormAdapter


def _geojson(adapter):
    return json.loads(adapter.get_storms_as_geojson())


def _by_type(geojson, feature_type):
    return [
        f for f in geojson["features"] if f["properties"]["feature_type"] == feature_type
    ]


def test_update_storm_creates_master_record_and_cone():
    adapter = FakeStormAdapter()
    adapter.update_storm(
        "AL012026",
        "Alpha",
        [(-80.0, 25.0), (-79.0, 26.0), (-78.0, 25.5)],
        [],
    )
    geojson = _geojson(adapter)
    cones = _by_type(geojson, "CONE")
    assert len(cones) == 1
    assert cones[0]["properties"]["sid"] == "AL012026"
    assert cones[0]["properties"]["name"] == "Alpha"
    assert cones[0]["geometry"]["type"] == "Polygon"


def test_update_storm_replaces_old_track_points():
    adapter = FakeStormAdapter()
    now = datetime.now(timezone.utc)
    track = [
        {"LAT": 25.0, "LON": -80.0, "TIME": now, "TYPE": "PAST", "TAU": 0},
        {"LAT": 25.5, "LON": -79.5, "TIME": now, "TYPE": "CURRENT", "TAU": 0},
    ]
    adapter.update_storm("AL012026", "Alpha", [], track)
    # Re-upsert with a different, shorter track - old points must be gone
    new_track = [{"LAT": 30.0, "LON": -70.0, "TIME": now, "TYPE": "CURRENT", "TAU": 0}]
    adapter.update_storm("AL012026", "Alpha", [], new_track)
    points = _by_type(_geojson(adapter), "POINT")
    assert len(points) == 1
    assert points[0]["geometry"]["coordinates"] == [-70.0, 30.0]


def test_update_storm_cone_updates_cone_geom_only():
    adapter = FakeStormAdapter()
    adapter.update_storm("AL012026", "Alpha", [(-80.0, 25.0), (-79.0, 26.0), (-78.0, 25.5)], [])
    adapter.update_storm_cone(
        "AL012026", {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]}
    )
    cones = _by_type(_geojson(adapter), "CONE")
    assert len(cones) == 1
    assert cones[0]["geometry"]["coordinates"] == [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]


def test_get_storms_as_geojson_track_past_line_needs_two_points():
    adapter = FakeStormAdapter()
    now = datetime.now(timezone.utc)
    # Only one PAST point -> no TRACK_PAST line (count(geom) > 1 required)
    adapter.update_storm(
        "AL012026", "Alpha", [], [{"LAT": 25.0, "LON": -80.0, "TIME": now, "TYPE": "PAST", "TAU": 0}]
    )
    assert _by_type(_geojson(adapter), "TRACK_PAST") == []

    adapter.update_storm(
        "AL012026",
        "Alpha",
        [],
        [
            {"LAT": 25.0, "LON": -80.0, "TIME": now, "TYPE": "PAST", "TAU": 0},
            {"LAT": 25.5, "LON": -79.5, "TIME": now + timedelta(hours=6), "TYPE": "CURRENT", "TAU": 0},
        ],
    )
    tracks = _by_type(_geojson(adapter), "TRACK_PAST")
    assert len(tracks) == 1
    assert tracks[0]["geometry"]["type"] == "LineString"


def test_get_storms_as_geojson_track_forecast_line():
    adapter = FakeStormAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_storm(
        "AL012026",
        "Alpha",
        [],
        [
            {"LAT": 25.5, "LON": -79.5, "TIME": now, "TYPE": "CURRENT", "TAU": 0},
            {"LAT": 26.0, "LON": -79.0, "TIME": now + timedelta(hours=6), "TYPE": "FORECAST", "TAU": 6},
        ],
    )
    tracks = _by_type(_geojson(adapter), "TRACK_FORECAST")
    assert len(tracks) == 1


def test_get_storms_as_geojson_points_include_name_from_storm():
    adapter = FakeStormAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_storm(
        "AL012026", "Alpha", [], [{"LAT": 25.0, "LON": -80.0, "TIME": now, "TYPE": "CURRENT", "TAU": 0}]
    )
    points = _by_type(_geojson(adapter), "POINT")
    assert len(points) == 1
    assert points[0]["properties"]["name"] == "Alpha"
    assert points[0]["properties"]["sid"] == "AL012026"
    assert points[0]["properties"]["record_type"] == "CURRENT"


def test_get_storms_as_geojson_empty():
    adapter = FakeStormAdapter()
    assert _geojson(adapter) == {"type": "FeatureCollection", "features": []}


def test_prune_expired_storms_removes_old_storms_and_their_tracks():
    adapter = FakeStormAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_storm(
        "OLD01", "Old", [], [{"LAT": 1.0, "LON": 1.0, "TIME": now, "TYPE": "CURRENT", "TAU": 0}]
    )
    adapter._storms["OLD01"]["updated_at"] = now - timedelta(days=10)
    adapter.update_storm(
        "NEW01", "New", [], [{"LAT": 2.0, "LON": 2.0, "TIME": now, "TYPE": "CURRENT", "TAU": 0}]
    )

    adapter.prune_expired_storms(expiry_days=4)

    geojson = _geojson(adapter)
    sids = {f["properties"]["sid"] for f in geojson["features"]}
    assert "OLD01" not in sids
    assert "NEW01" in sids
    # FK CASCADE: OLD01's track points must be gone too
    assert "OLD01" not in adapter._tracks
