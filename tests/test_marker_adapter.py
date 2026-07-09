import json

from atmos_gl.db.marker_adapter import FakeMarkerAdapter


def _row(id_, name="Wellington", lat=-41.28, lon=174.77, **kw):
    return {
        "id": id_,
        "name": name,
        "kind": kw.get("kind", "place"),
        "country": kw.get("country", "NZ"),
        "priority": kw.get("priority", 1),
        "pop": kw.get("pop", 200000),
        "capital": kw.get("capital", True),
        "color": kw.get("color", "#fff"),
        "timezone": kw.get("timezone", "Pacific/Auckland"),
        "lat": lat,
        "lon": lon,
    }


def _geojson(adapter):
    return json.loads(adapter.get_markers_as_geojson())


def test_upsert_markers_inserts_new_rows():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1")])
    geojson = _geojson(adapter)
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["name"] == "Wellington"


def test_upsert_markers_empty_rows_is_noop():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([])
    assert _geojson(adapter) == {"type": "FeatureCollection", "features": []}


def test_upsert_markers_conflict_updates_static_fields_but_not_weather():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1", name="Old Name", priority=1)])
    adapter.update_marker_weather(
        [{"id": "m1", "t": 20.0, "rh": 50, "ws": 5.0, "wd": 180, "valid_time": "2026-07-01T00:00:00"}]
    )
    adapter.upsert_markers([_row("m1", name="New Name", priority=2)])

    feature = _geojson(adapter)["features"][0]
    assert feature["properties"]["name"] == "New Name"
    assert feature["properties"]["priority"] == 2
    # wx_* fields must survive a re-upsert untouched
    assert feature["properties"]["t"] == 20.0
    assert feature["properties"]["rh"] == 50


def test_delete_markers_not_in_removes_missing_rows():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("keep"), _row("gone", name="Gone")])
    deleted = adapter.delete_markers_not_in(["keep"])
    assert deleted == 1
    ids = {f["properties"]["name"] for f in _geojson(adapter)["features"]}
    assert ids == {"Wellington"}


def test_delete_markers_not_in_empty_ids_is_noop_guard():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("keep")])
    deleted = adapter.delete_markers_not_in([])
    assert deleted == 0
    assert len(_geojson(adapter)["features"]) == 1


def test_update_marker_weather_sets_wx_fields():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1")])
    adapter.update_marker_weather(
        [{"id": "m1", "t": 18.5, "rh": 65, "ws": 3.2, "wd": 90, "valid_time": "2026-07-01T12:00:00"}]
    )
    feature = _geojson(adapter)["features"][0]
    assert feature["properties"]["t"] == 18.5
    assert feature["properties"]["rh"] == 65
    assert feature["properties"]["ws"] == 3.2
    assert feature["properties"]["wd"] == 90
    assert feature["properties"]["wx_valid_time"] == "2026-07-01T12:00:00"


def test_update_marker_weather_unmatched_id_is_noop():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1")])
    adapter.update_marker_weather([{"id": "nonexistent", "t": 1, "rh": 1, "ws": 1, "wd": 1, "valid_time": None}])
    feature = _geojson(adapter)["features"][0]
    assert feature["properties"]["t"] is None


def test_update_marker_weather_empty_updates_is_noop():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1")])
    adapter.update_marker_weather([])
    assert len(_geojson(adapter)["features"]) == 1


def test_get_markers_as_geojson_shape():
    adapter = FakeMarkerAdapter()
    adapter.upsert_markers([_row("m1")])
    feature = _geojson(adapter)["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"] == {"type": "Point", "coordinates": [174.77, -41.28]}
    assert feature["properties"]["kind"] == "place"
    assert feature["properties"]["country"] == "NZ"
    assert feature["properties"]["t"] is None
    assert feature["properties"]["wx_valid_time"] is None


def test_get_markers_as_geojson_empty():
    adapter = FakeMarkerAdapter()
    assert _geojson(adapter) == {"type": "FeatureCollection", "features": []}
