from datetime import datetime, timedelta, timezone

from worldmap.db.quake_adapter import FakeQuakeAdapter


def _iso(dt):
    return dt.isoformat()


def test_update_quake_inserts_new_quake():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("q1", 5.5, 10.0, "Somewhere", _iso(now), -40.0, 175.0)
    geojson = _quakes(adapter)
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["id"] == "q1"


def test_update_quake_conflict_updates_mutable_fields_only():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("q1", 5.0, 10.0, "Old Place", _iso(now), -40.0, 175.0)
    adapter.update_quake("q1", 6.0, 20.0, "New Place", _iso(now), 1.0, 1.0)
    geojson = _quakes(adapter)
    feature = geojson["features"][0]
    assert feature["properties"]["mag"] == 6.0
    assert feature["properties"]["depth"] == 20.0
    assert feature["properties"]["place"] == "New Place"
    # lat/lon (geometry) is NOT in the ON CONFLICT SET list, so it must survive unchanged
    assert feature["geometry"]["coordinates"] == [175.0, -40.0]


def test_get_quakes_as_geojson_filters_by_min_mag():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("big", 5.0, 10.0, "A", _iso(now), -40.0, 175.0)
    adapter.update_quake("small", 2.0, 10.0, "B", _iso(now), -40.0, 175.0)
    geojson = _quakes(adapter, min_mag=3.5)
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert ids == {"big"}


def test_get_quakes_as_geojson_filters_by_expiry_hours():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("recent", 5.0, 10.0, "A", _iso(now - timedelta(hours=1)), -40.0, 175.0)
    adapter.update_quake("old", 5.0, 10.0, "B", _iso(now - timedelta(hours=20)), -40.0, 175.0)
    geojson = _quakes(adapter, expiry_hours=12)
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert ids == {"recent"}


def test_get_quakes_as_geojson_is_recent_flag():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("recent", 5.0, 10.0, "A", _iso(now - timedelta(hours=1)), -40.0, 175.0)
    adapter.update_quake("stale", 5.0, 10.0, "B", _iso(now - timedelta(hours=6)), -40.0, 175.0)
    geojson = _quakes(adapter, recent_hours=3)
    by_id = {f["properties"]["id"]: f["properties"] for f in geojson["features"]}
    assert by_id["recent"]["is_recent"] is True
    assert by_id["stale"]["is_recent"] is False


def test_get_quakes_as_geojson_shape():
    adapter = FakeQuakeAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_quake("q1", 5.5, 12.3, "Testville", _iso(now), -40.0, 175.0)
    geojson = _quakes(adapter)
    feature = geojson["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [175.0, -40.0]
    assert feature["properties"]["mag"] == 5.5
    assert feature["properties"]["depth"] == 12.3
    assert feature["properties"]["place"] == "Testville"
    assert feature["properties"]["age_minutes"] < 1.0


def test_get_quakes_as_geojson_empty():
    adapter = FakeQuakeAdapter()
    geojson = _quakes(adapter)
    assert geojson == {"type": "FeatureCollection", "features": []}


def _quakes(adapter, **kwargs):
    import json

    return json.loads(adapter.get_quakes_as_geojson(**kwargs))
