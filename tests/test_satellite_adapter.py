from worldmap.db.satellite_adapter import FakeSatelliteAdapter


def test_update_satellite_inserts_new_satellite():
    adapter = FakeSatelliteAdapter()
    adapter.update_satellite(25544, "ISS (ZARYA)", {"OBJECT_NAME": "ISS (ZARYA)"}, "2026-07-01T00:00:00")
    rows = adapter.get_satellites_by_names(["ISS (ZARYA)"])
    assert len(rows) == 1
    assert rows[0]["norad_id"] == 25544
    assert rows[0]["name"] == "ISS (ZARYA)"
    assert rows[0]["omm"] == {"OBJECT_NAME": "ISS (ZARYA)"}
    assert rows[0]["epoch"] == "2026-07-01T00:00:00"


def test_update_satellite_conflict_updates_name_omm_epoch():
    adapter = FakeSatelliteAdapter()
    adapter.update_satellite(25544, "OLD NAME", {"v": 1}, "2026-07-01T00:00:00")
    adapter.update_satellite(25544, "NEW NAME", {"v": 2}, "2026-07-02T00:00:00")
    rows = adapter.get_satellites_by_names(["NEW NAME"])
    assert len(rows) == 1
    assert rows[0]["name"] == "NEW NAME"
    assert rows[0]["omm"] == {"v": 2}
    assert rows[0]["epoch"] == "2026-07-02T00:00:00"


def test_get_satellites_by_names_filters_by_name():
    adapter = FakeSatelliteAdapter()
    adapter.update_satellite(1, "Alpha", {}, None)
    adapter.update_satellite(2, "Beta", {}, None)
    adapter.update_satellite(3, "Gamma", {}, None)
    rows = adapter.get_satellites_by_names(["Alpha", "Gamma"])
    names = {r["name"] for r in rows}
    assert names == {"Alpha", "Gamma"}


def test_get_satellites_by_names_empty_names_returns_empty():
    adapter = FakeSatelliteAdapter()
    adapter.update_satellite(1, "Alpha", {}, None)
    assert adapter.get_satellites_by_names([]) == []


def test_get_satellites_by_names_no_match_returns_empty():
    adapter = FakeSatelliteAdapter()
    adapter.update_satellite(1, "Alpha", {}, None)
    assert adapter.get_satellites_by_names(["Nonexistent"]) == []
