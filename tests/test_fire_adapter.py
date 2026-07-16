from datetime import datetime, timedelta, timezone

from atmos_gl.db.fire_adapter import FakeFireAdapter


def _iso(dt):
    return dt.isoformat()


def _row(fire_id, lat, lon, brightness, frp, confidence, satellite, daynight, acq_time_iso):
    return {
        "id": fire_id,
        "lat": lat,
        "lon": lon,
        "brightness": brightness,
        "frp": frp,
        "confidence": confidence,
        "satellite": satellite,
        "daynight": daynight,
        "acq_time": acq_time_iso,
    }


def test_upsert_fires_inserts_new_fire():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires([_row("f1", -40.0, 175.0, 330.5, 12.3, "nominal", "N", "D", _iso(now))])
    geojson = _fires(adapter)
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["id"] == "f1"


def test_upsert_fires_conflict_updates_mutable_fields_only():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires([_row("f1", -40.0, 175.0, 320.0, 10.0, "low", "N", "D", _iso(now))])
    adapter.upsert_fires([_row("f1", 1.0, 1.0, 340.0, 20.0, "high", "N1", "N", _iso(now))])
    geojson = _fires(adapter)
    feature = geojson["features"][0]
    assert feature["properties"]["brightness"] == 340.0
    assert feature["properties"]["frp"] == 20.0
    assert feature["properties"]["confidence"] == "high"
    # lat/lon (geometry) is NOT in the ON CONFLICT SET list, so it must survive unchanged
    assert feature["geometry"]["coordinates"] == [175.0, -40.0]


def test_upsert_fires_handles_multiple_rows_in_one_call():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires(
        [
            _row("a", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now)),
            _row("b", -41.0, 176.0, 331.0, 11.0, "nominal", "N", "D", _iso(now)),
        ]
    )
    ids = {f["properties"]["id"] for f in _fires(adapter)["features"]}
    assert ids == {"a", "b"}


def test_get_fires_as_geojson_filters_by_min_confidence():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires(
        [
            _row("hi", -40.0, 175.0, 330.0, 10.0, "high", "N", "D", _iso(now)),
            _row("lo", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now)),
        ]
    )
    geojson = _fires(adapter, min_confidence="nominal")
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert ids == {"hi"}


def test_get_fires_as_geojson_filters_by_expiry_hours():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires(
        [
            _row("recent", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now - timedelta(hours=1))),
            _row("old", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now - timedelta(hours=30))),
        ]
    )
    geojson = _fires(adapter, expiry_hours=24)
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert ids == {"recent"}


def test_get_fires_as_geojson_filters_by_max_frp():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires(
        [
            _row("plausible", -40.0, 175.0, 330.0, 800.0, "high", "N", "D", _iso(now)),
            _row("flare", -40.0, 175.0, 330.0, 12444.0, "nominal", "N", "N", _iso(now)),
        ]
    )
    geojson = _fires(adapter, max_frp=5000.0)
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert ids == {"plausible"}


def test_get_fires_as_geojson_shape():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires([_row("f1", -40.0, 175.0, 330.5, 12.3, "nominal", "N", "D", _iso(now))])
    geojson = _fires(adapter)
    feature = geojson["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [175.0, -40.0]
    assert feature["properties"]["brightness"] == 330.5
    assert feature["properties"]["frp"] == 12.3
    assert feature["properties"]["satellite"] == "N"
    assert feature["properties"]["daynight"] == "D"
    assert feature["properties"]["age_minutes"] < 1.0


def test_get_fires_as_geojson_empty():
    adapter = FakeFireAdapter()
    geojson = _fires(adapter)
    assert geojson == {"type": "FeatureCollection", "features": []}


def test_delete_expired_removes_only_old_rows():
    adapter = FakeFireAdapter()
    now = datetime.now(timezone.utc)
    adapter.upsert_fires(
        [
            _row("recent", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now - timedelta(hours=1))),
            _row("old", -40.0, 175.0, 330.0, 10.0, "low", "N", "D", _iso(now - timedelta(hours=30))),
        ]
    )

    deleted = adapter.delete_expired(expiry_hours=24)

    assert deleted == 1
    remaining_ids = {f["properties"]["id"] for f in _fires(adapter, expiry_hours=999)["features"]}
    assert remaining_ids == {"recent"}


def _fires(adapter, **kwargs):
    import json

    return json.loads(adapter.get_fires_as_geojson(**kwargs))
