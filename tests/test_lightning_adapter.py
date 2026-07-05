from datetime import datetime, timedelta, timezone

from worldmap.db.lightning_adapter import FakeLightningAdapter


def _iso(dt):
    return dt.isoformat()


def test_update_lightning_strike_inserts_new_strike():
    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("s1", -40.0, 175.0, "A", _iso(now))
    hits = adapter.get_lightning_in_region(170.0, -45.0, 180.0, -35.0, expiry_minutes=60)
    assert len(hits) == 1
    assert hits[0]["lat"] == -40.0
    assert hits[0]["lon"] == 175.0


def test_update_lightning_strike_conflict_does_nothing():
    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("s1", -40.0, 175.0, "A", _iso(now))
    # same id, different data: original values must survive (ON CONFLICT DO NOTHING)
    adapter.update_lightning_strike("s1", 10.0, 10.0, "B", _iso(now))
    hits = adapter.get_lightning_in_region(-180.0, -90.0, 180.0, 90.0, expiry_minutes=60)
    assert len(hits) == 1
    assert hits[0]["lat"] == -40.0
    assert hits[0]["lon"] == 175.0


def test_get_lightning_in_region_filters_by_bbox():
    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("inside", -40.0, 175.0, "A", _iso(now))
    adapter.update_lightning_strike("outside", 40.0, 10.0, "A", _iso(now))
    hits = adapter.get_lightning_in_region(170.0, -45.0, 180.0, -35.0, expiry_minutes=60)
    assert len(hits) == 1
    assert hits[0]["lat"] == -40.0


def test_get_lightning_in_region_filters_by_expiry():
    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("recent", -40.0, 175.0, "A", _iso(now - timedelta(minutes=10)))
    adapter.update_lightning_strike("old", -40.0, 175.0, "A", _iso(now - timedelta(minutes=90)))
    hits = adapter.get_lightning_in_region(170.0, -45.0, 180.0, -35.0, expiry_minutes=60)
    assert len(hits) == 1


def test_get_lightning_as_geojson_filters_by_expiry_hours():
    import json

    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("recent", -40.0, 175.0, "A", _iso(now - timedelta(hours=1)))
    adapter.update_lightning_strike("old", -40.0, 175.0, "A", _iso(now - timedelta(hours=5)))
    geojson = json.loads(adapter.get_lightning_as_geojson(expiry_hours=2))
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["id"] == "recent"


def test_get_lightning_as_geojson_shape():
    import json

    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("s1", -40.0, 175.0, "GOOD", _iso(now))
    geojson = json.loads(adapter.get_lightning_as_geojson(expiry_hours=2))
    feature = geojson["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["coordinates"] == [175.0, -40.0]
    assert feature["properties"]["quality"] == "GOOD"
    assert feature["properties"]["age_minutes"] < 1.0


def test_get_lightning_as_geojson_empty():
    import json

    adapter = FakeLightningAdapter()
    geojson = json.loads(adapter.get_lightning_as_geojson())
    assert geojson == {"type": "FeatureCollection", "features": []}


def test_prune_lightning_removes_old_rows_only():
    adapter = FakeLightningAdapter()
    now = datetime.now(timezone.utc)
    adapter.update_lightning_strike("recent", -40.0, 175.0, "A", _iso(now - timedelta(hours=1)))
    adapter.update_lightning_strike("old", -40.0, 175.0, "A", _iso(now - timedelta(hours=48)))
    removed = adapter.prune_lightning(expiry_hours=24)
    assert removed == 1
    remaining = adapter.get_lightning_in_region(-180.0, -90.0, 180.0, 90.0, expiry_minutes=999999)
    assert len(remaining) == 1
