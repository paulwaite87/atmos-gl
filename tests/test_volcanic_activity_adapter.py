import json

from atmos_gl.db.volcanic_activity_adapter import FakeVolcanicActivityAdapter


def _geojson(adapter):
    return json.loads(adapter.get_activity_as_geojson())


def test_upsert_activity_inserts_new_volcano():
    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity(
        "311120", "Great Sitkin", "United States", 52.08, -176.13,
        "Continuing Eruptive Activity", "Lava effusion continues.", None, None, None,
    )
    geojson = _geojson(adapter)
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["name"] == "Great Sitkin"


def test_hans_only_upsert_does_not_clobber_existing_gvp_coordinate():
    """A HANS-elevated volcano absent from this week's GVP report still calls
    upsert_activity, but with name/country/lat/lon=None -- those must NOT overwrite a
    previously-recorded GVP sighting (issue #253's HANS/GVP liveness discussion)."""
    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity(
        "311120", "Great Sitkin", "United States", 52.08, -176.13,
        "Continuing Eruptive Activity", "Report text.", None, None, None,
    )
    adapter.upsert_activity(
        "311120", None, None, None, None, None, None, "ORANGE", "WATCH", "https://example.com/notice",
    )
    geojson = _geojson(adapter)
    feature = geojson["features"][0]
    assert feature["properties"]["name"] == "Great Sitkin"
    assert feature["geometry"]["coordinates"] == [-176.13, 52.08]
    assert feature["properties"]["hans_alert_level"] == "WATCH"
    assert feature["properties"]["hans_color_code"] == "ORANGE"


def test_is_new_derived_from_activity_type_prefix():
    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity("1", "New One", "Country", 0.0, 0.0, "New Unrest", None, None, None, None)
    adapter.upsert_activity("2", "Old One", "Country", 0.0, 0.0, "Continuing Eruptive Activity", None, None, None, None)
    by_name = {f["properties"]["name"]: f["properties"]["is_new"] for f in _geojson(adapter)["features"]}
    assert by_name == {"New One": True, "Old One": False}


def test_volcano_with_no_coordinate_ever_recorded_is_excluded():
    """A HANS-elevated volcano never once seen in any GVP report has no lat/lon at
    all -- nothing to plot, so it's excluded rather than rendered at (None, None)."""
    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity("999", None, None, None, None, None, None, "RED", "WARNING", None)
    assert _geojson(adapter)["features"] == []


def test_get_activity_as_geojson_shape():
    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity(
        "311120", "Great Sitkin", "United States", 52.08, -176.13,
        "Continuing Eruptive Activity", "Report text.", "GREEN", "NORMAL", "https://example.com",
    )
    feature = _geojson(adapter)["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [-176.13, 52.08]
    assert feature["properties"] == {
        "vnum": "311120",
        "name": "Great Sitkin",
        "country": "United States",
        "activity_type": "Continuing Eruptive Activity",
        "is_new": False,
        "report_description": "Report text.",
        "hans_color_code": "GREEN",
        "hans_alert_level": "NORMAL",
        "hans_notice_url": "https://example.com",
    }


def test_get_activity_as_geojson_empty():
    adapter = FakeVolcanicActivityAdapter()
    assert _geojson(adapter) == {"type": "FeatureCollection", "features": []}


def test_prune_expired_activity_removes_stale_rows_only():
    from datetime import datetime, timedelta, timezone

    adapter = FakeVolcanicActivityAdapter()
    adapter.upsert_activity("stale", "Stale", "Country", 0.0, 0.0, "Continuing Eruptive Activity", None, None, None, None)
    adapter.upsert_activity("fresh", "Fresh", "Country", 0.0, 0.0, "New Unrest", None, None, None, None)
    adapter._activity["stale"]["last_seen_at"] = datetime.now(timezone.utc) - timedelta(days=20)

    removed = adapter.prune_expired_activity(14)

    assert removed == 1
    names = {f["properties"]["name"] for f in _geojson(adapter)["features"]}
    assert names == {"Fresh"}
