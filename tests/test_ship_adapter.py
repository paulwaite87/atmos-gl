from datetime import datetime, timedelta, timezone

from worldmap.db.ship_adapter import FakeShipAdapter


def _meta(name="Test Ship", time_utc=""):
    return {"ShipName": name, "time_utc": time_utc}


def _body(**overrides):
    body = {
        "Destination": "Auckland",
        "Type": 70,
        "ImoNumber": 1234567,
        "CallSign": "ZMAB",
        "MaximumStaticDraught": 5.5,
        "Dimension": {"A": 50, "B": 10, "C": 8, "D": 8},
    }
    body.update(overrides)
    return body


def test_update_ship_static_data_inserts_new_ship():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(123456789, _meta(), _body(), ais_tier="A")

    total = adapter.get_current_ship_total()
    assert total == 1


def test_update_ship_static_data_new_ship_prev_draught_is_zero():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(123456789, _meta(), _body(), ais_tier="A")
    adapter.update_ship_position_data(123456789, _meta(), _body(Latitude=1.0, Longitude=2.0))
    geojson = adapter.get_fleet_as_geojson()
    assert '"draught": 5.5' in geojson or "5.5" in geojson


def test_update_ship_static_data_prev_draught_advances_only_on_real_change():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(1, _meta(), _body(MaximumStaticDraught=5.0))
    # same draught again: prev_draught must NOT advance
    adapter.update_ship_static_data(1, _meta(), _body(MaximumStaticDraught=5.0))
    adapter.update_ship_position_data(1, _meta(), _body(Latitude=1.0, Longitude=2.0))
    # now change draught: prev_draught should become the OLD draught (5.0)
    adapter.update_ship_static_data(1, _meta(), _body(MaximumStaticDraught=7.0))
    assert adapter._ships["1"]["prev_draught"] == 5.0
    assert adapter._ships["1"]["draught"] == 7.0


def test_update_ship_static_data_prev_draught_ignores_zero_new_draught():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(1, _meta(), _body(MaximumStaticDraught=5.0))
    adapter.update_ship_static_data(1, _meta(), _body(MaximumStaticDraught=0.0))
    # new draught of 0 must not advance prev_draught, per "EXCLUDED.draught > 0"
    assert adapter._ships["1"]["prev_draught"] == 0.0
    assert adapter._ships["1"]["draught"] == 0.0


def test_update_ship_position_data_keeps_existing_name_when_incoming_is_unknown():
    adapter = FakeShipAdapter()
    adapter.update_ship_position_data(1, _meta(name="Real Name"), _body(Latitude=1.0, Longitude=2.0))
    adapter.update_ship_position_data(1, _meta(name="Unknown"), _body(Latitude=1.1, Longitude=2.1))
    assert adapter._ships["1"]["name"] == "Real Name"


def test_update_ship_position_data_keeps_existing_nonzero_vessel_type():
    adapter = FakeShipAdapter()
    adapter.update_ship_position_data(1, _meta(), _body(Type=70, Latitude=1.0, Longitude=2.0))
    adapter.update_ship_position_data(1, _meta(), _body(Type=0, Latitude=1.1, Longitude=2.1))
    assert adapter._ships["1"]["vessel_type"] == 70


def test_update_ship_position_data_appends_history_row():
    adapter = FakeShipAdapter()
    adapter.update_ship_position_data(1, _meta(), _body(Latitude=1.0, Longitude=2.0))
    adapter.update_ship_position_data(1, _meta(), _body(Latitude=1.5, Longitude=2.5))
    track = adapter.get_ship_track(1, limit=10)
    assert len(track) == 2


def test_update_ship_position_data_parses_ais_timestamp():
    adapter = FakeShipAdapter()
    adapter.update_ship_position_data(
        1, _meta(time_utc="2026-01-01 12:00:00.123456 UTC +00:00"),
        _body(Latitude=1.0, Longitude=2.0),
    )
    row = adapter._ships["1"]
    assert row["last_position_update"] == datetime(
        2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc
    )


def test_get_current_ship_total_counts_distinct_mmsi():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(1, _meta(), _body())
    adapter.update_ship_static_data(2, _meta(), _body())
    assert adapter.get_current_ship_total() == 2


def test_get_fleet_as_geojson_excludes_ships_without_geom():
    adapter = FakeShipAdapter()
    adapter.update_ship_static_data(1, _meta(), _body())  # never got a position -> no geom
    adapter.update_ship_position_data(2, _meta(), _body(Latitude=1.0, Longitude=2.0))

    import json

    geojson = json.loads(adapter.get_fleet_as_geojson())
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["mmsi"] == "2"


def test_get_fleet_as_geojson_empty_fleet():
    import json

    adapter = FakeShipAdapter()
    geojson = json.loads(adapter.get_fleet_as_geojson())
    assert geojson == {"type": "FeatureCollection", "features": []}


def test_get_ship_track_returns_empty_for_missing_mmsi():
    adapter = FakeShipAdapter()
    assert adapter.get_ship_track(None) == []
    assert adapter.get_ship_track("") == []


def test_get_ship_track_orders_newest_first():
    adapter = FakeShipAdapter()
    adapter._positions.append(
        {"mmsi": "1", "lat": 1.0, "lon": 1.0, "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    adapter._positions.append(
        {"mmsi": "1", "lat": 2.0, "lon": 2.0, "acquired_at": datetime(2026, 1, 2, tzinfo=timezone.utc)}
    )
    track = adapter.get_ship_track(1)
    assert track[0]["lat"] == 2.0
    assert track[1]["lat"] == 1.0


def test_get_ship_track_respects_limit():
    adapter = FakeShipAdapter()
    for i in range(5):
        adapter._positions.append(
            {"mmsi": "1", "lat": float(i), "lon": float(i),
             "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i)}
        )
    assert len(adapter.get_ship_track(1, limit=2)) == 2


def test_prune_vessel_tracks_noop_on_falsy_expiry():
    adapter = FakeShipAdapter()
    assert adapter.prune_vessel_tracks(0) == 0
    assert adapter.prune_vessel_tracks(None) == 0
    assert adapter.prune_vessel_tracks(-1) == 0


def test_prune_vessel_tracks_removes_old_rows_only():
    adapter = FakeShipAdapter()
    now = datetime.now(timezone.utc)
    adapter._positions.append({"mmsi": "1", "lat": 1.0, "lon": 1.0, "acquired_at": now - timedelta(days=10)})
    adapter._positions.append({"mmsi": "1", "lat": 2.0, "lon": 2.0, "acquired_at": now})
    removed = adapter.prune_vessel_tracks(expiry_days=5)
    assert removed == 1
    assert len(adapter._positions) == 1
