from atmos_gl.db.region_adapter import FakeRegionAdapter


def _add(adapter, label, lon_min, lat_min, lon_max, lat_max):
    adapter._regions[label] = {
        "lon_min": lon_min,
        "lat_min": lat_min,
        "lon_max": lon_max,
        "lat_max": lat_max,
    }


def test_get_region_definition_returns_bbox():
    adapter = FakeRegionAdapter()
    _add(adapter, "NZ", 165.0, -47.0, 179.0, -34.0)
    bbox = adapter.get_region_definition("NZ")
    assert bbox == {"lon_min": 165.0, "lat_min": -47.0, "lon_max": 179.0, "lat_max": -34.0}


def test_get_region_definition_missing_label_returns_none():
    adapter = FakeRegionAdapter()
    assert adapter.get_region_definition("Nonexistent") is None


def test_get_all_regions_is_alphabetical_and_includes_bbox():
    adapter = FakeRegionAdapter()
    _add(adapter, "Beta", 0, 0, 1, 1)
    _add(adapter, "Alpha", 165.0, -47.0, 179.0, -34.0)
    rows = adapter.get_all_regions()
    assert [r["label"] for r in rows] == ["Alpha", "Beta"]
    assert rows[0]["lon_min"] == 165.0
    assert rows[0]["lat_max"] == -34.0


def test_get_all_regions_empty_when_no_regions():
    adapter = FakeRegionAdapter()
    assert adapter.get_all_regions() == []
