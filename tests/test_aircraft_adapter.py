from datetime import datetime, timedelta, timezone

from atmos_gl.db.aircraft_adapter import FakeAircraftAdapter


def _record(hex="a1b2c3", **overrides):
    base = {
        "hex": hex,
        "flight": "TEST123",
        "r": "ZK-TST",
        "t": "B738",
        "lat": -36.8,
        "lon": 174.7,
        "alt_baro": 35000,
        "gs": 450.0,
        "track": 90.0,
        "baro_rate": 0,
        "nav_altitude_mcp": 36000,
        "squawk": "2000",
    }
    base.update(overrides)
    return base


def test_record_sighting_inserts_new_aircraft():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record())
    assert adapter.get_current_aircraft_total() == 1


def test_record_sighting_returns_false_for_missing_hex():
    adapter = FakeAircraftAdapter()
    assert adapter.record_sighting(_record(hex="")) is False
    assert adapter.get_current_aircraft_total() == 0


def test_record_sighting_ground_sentinel_sets_on_ground_and_clears_altitude():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(alt_baro="ground"))
    row = adapter._aircraft["a1b2c3"]
    assert row["on_ground"] is True
    assert row["alt_baro_ft"] is None


def test_record_sighting_keeps_existing_registration_when_incoming_is_missing():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(r="ZK-REAL"))
    adapter.record_sighting(_record(r=None))
    assert adapter._aircraft["a1b2c3"]["registration"] == "ZK-REAL"


def test_record_sighting_appends_history_row():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(lat=1.0, lon=2.0))
    adapter.record_sighting(_record(lat=1.5, lon=2.5))
    track = adapter.get_aircraft_track("a1b2c3", limit=10)
    assert len(track) == 2


def test_record_sightings_batch_skips_records_with_no_hex():
    adapter = FakeAircraftAdapter()
    count = adapter.record_sightings([_record(hex="aa1111"), _record(hex="")])
    assert count == 1
    assert adapter.get_current_aircraft_total() == 1


def test_get_current_aircraft_total_counts_distinct_hex():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111"))
    adapter.record_sighting(_record(hex="bb2222"))
    assert adapter.get_current_aircraft_total() == 2


def test_get_fleet_as_geojson_excludes_aircraft_without_geom():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", lat=None, lon=None))  # never positioned
    adapter.record_sighting(_record(hex="bb2222", lat=1.0, lon=2.0))

    import json

    geojson = json.loads(adapter.get_fleet_as_geojson())
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["hex"] == "bb2222"


def test_get_fleet_as_geojson_empty_fleet():
    import json

    adapter = FakeAircraftAdapter()
    geojson = json.loads(adapter.get_fleet_as_geojson())
    assert geojson == {"type": "FeatureCollection", "features": []}


def test_get_aircraft_track_returns_empty_for_missing_hex():
    adapter = FakeAircraftAdapter()
    assert adapter.get_aircraft_track(None) == []
    assert adapter.get_aircraft_track("") == []


def test_get_aircraft_track_orders_newest_first():
    adapter = FakeAircraftAdapter()
    adapter._tracks.append(
        {"hex": "a1b2c3", "lat": 1.0, "lon": 1.0, "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    adapter._tracks.append(
        {"hex": "a1b2c3", "lat": 2.0, "lon": 2.0, "acquired_at": datetime(2026, 1, 2, tzinfo=timezone.utc)}
    )
    track = adapter.get_aircraft_track("a1b2c3")
    assert track[0]["lat"] == 2.0
    assert track[1]["lat"] == 1.0


def test_get_aircraft_track_respects_limit():
    adapter = FakeAircraftAdapter()
    for i in range(5):
        adapter._tracks.append(
            {"hex": "a1b2c3", "lat": float(i), "lon": float(i),
             "acquired_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i)}
        )
    assert len(adapter.get_aircraft_track("a1b2c3", limit=2)) == 2


def test_prune_aircraft_tracks_noop_on_falsy_expiry():
    adapter = FakeAircraftAdapter()
    assert adapter.prune_aircraft_tracks(0) == 0
    assert adapter.prune_aircraft_tracks(None) == 0
    assert adapter.prune_aircraft_tracks(-1) == 0


def test_prune_aircraft_tracks_removes_old_rows_only():
    adapter = FakeAircraftAdapter()
    now = datetime.now(timezone.utc)
    adapter._tracks.append({"hex": "a1b2c3", "lat": 1.0, "lon": 1.0, "acquired_at": now - timedelta(hours=48)})
    adapter._tracks.append({"hex": "a1b2c3", "lat": 2.0, "lon": 2.0, "acquired_at": now})
    removed = adapter.prune_aircraft_tracks(expiry_hours=24)
    assert removed == 1
    assert len(adapter._tracks) == 1


def test_record_interest_and_get_active_interest_round_trip():
    adapter = FakeAircraftAdapter()
    adapter.record_interest("viewer-1", west=0.0, south=0.0, east=1.0, north=1.0)
    active = adapter.get_active_interest(max_age_s=60.0)
    assert active == [(0.0, 0.0, 1.0, 1.0)]


def test_get_active_interest_excludes_stale_rows():
    adapter = FakeAircraftAdapter()
    adapter.record_interest("viewer-1", west=0.0, south=0.0, east=1.0, north=1.0)
    adapter._interest["viewer-1"]["last_seen_at"] = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert adapter.get_active_interest(max_age_s=30.0) == []


# ---- get_active_flight_positions: the priority feed AircraftCollector's route-
# enrichment tick reads from (issue #215's route-lookup follow-on) -----------------

def test_get_active_flight_positions_excludes_aircraft_without_a_callsign():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", flight=None))
    adapter.record_sighting(_record(hex="bb2222", flight="ANZ423"))
    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=10)
    assert [p["callsign"] for p in positions] == ["ANZ423"]


def test_get_active_flight_positions_dedupes_by_callsign():
    """Two different physical aircraft (hexes) sharing one live callsign must only
    contribute ONE candidate -- a route lookup is per-callsign, not per-hex."""
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", flight="ANZ423"))
    adapter.record_sighting(_record(hex="bb2222", flight="ANZ423"))
    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=10)
    assert len(positions) == 1
    assert positions[0]["callsign"] == "ANZ423"


def test_get_active_flight_positions_prioritizes_callsigns_inside_an_active_viewport():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", flight="OUTSIDE1", lat=10.0, lon=10.0))
    adapter.record_sighting(_record(hex="bb2222", flight="INSIDE1", lat=0.5, lon=0.5))
    adapter.record_interest("viewer-1", west=0.0, south=0.0, east=1.0, north=1.0)

    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=10)
    assert positions[0]["callsign"] == "INSIDE1"


def test_get_active_flight_positions_ignores_an_expired_viewport():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", flight="A", lat=0.5, lon=0.5))
    adapter.record_interest("viewer-1", west=0.0, south=0.0, east=1.0, north=1.0)
    adapter._interest["viewer-1"]["last_seen_at"] = datetime.now(timezone.utc) - timedelta(seconds=120)

    # No active viewport -> no in-viewport boost, but the callsign is still returned.
    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=10)
    assert [p["callsign"] for p in positions] == ["A"]


def test_get_active_flight_positions_respects_the_limit():
    adapter = FakeAircraftAdapter()
    for i in range(5):
        adapter.record_sighting(_record(hex=f"hex{i}", flight=f"CALL{i}"))
    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=2)
    assert len(positions) == 2


def test_get_active_flight_positions_returns_lat_lng_shape():
    adapter = FakeAircraftAdapter()
    adapter.record_sighting(_record(hex="aa1111", flight="ANZ423", lat=-41.3, lon=174.8))
    positions = adapter.get_active_flight_positions(interest_max_age_s=30.0, limit=10)
    assert positions == [{"callsign": "ANZ423", "lat": -41.3, "lng": 174.8}]
