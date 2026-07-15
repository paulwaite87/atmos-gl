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


def test_get_priority_region_list_orders_primary_first():
    adapter = FakeRegionAdapter()
    _add(adapter, "Alpha", 0, 0, 1, 1)
    _add(adapter, "Beta", 0, 0, 1, 1)
    _add(adapter, "Gamma", 0, 0, 1, 1)
    labels = [r["label"] for r in adapter.get_priority_region_list("Gamma")]
    assert labels[0] == "Gamma"
    assert labels[1:] == sorted(labels[1:])


def test_get_priority_region_list_alphabetical_when_no_primary_match():
    adapter = FakeRegionAdapter()
    _add(adapter, "Beta", 0, 0, 1, 1)
    _add(adapter, "Alpha", 0, 0, 1, 1)
    labels = [r["label"] for r in adapter.get_priority_region_list("Whole World")]
    assert labels == ["Alpha", "Beta"]


def test_get_priority_region_list_includes_bbox():
    adapter = FakeRegionAdapter()
    _add(adapter, "NZ", 165.0, -47.0, 179.0, -34.0)
    rows = adapter.get_priority_region_list("NZ")
    assert rows[0]["lon_min"] == 165.0
    assert rows[0]["lat_max"] == -34.0
