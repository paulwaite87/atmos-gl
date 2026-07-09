import json

from atmos_gl.db.volcano_adapter import FakeVolcanoAdapter


def _geojson(adapter, vei_min=0, significant=False, date_codes=None):
    return json.loads(
        adapter.get_volcanoes_as_geojson(vei_min, significant, date_codes or [])
    )


def test_update_volcano_inserts_new_volcano():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("v1", "Ruapehu", -39.28, 175.57, 2, True, "D1")
    geojson = _geojson(adapter, date_codes=["D1"])
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["properties"]["name"] == "Ruapehu"


def test_update_volcano_conflict_updates_vei_significant_date_code_only():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("v1", "Ruapehu", -39.28, 175.57, 1, False, "D0")
    adapter.update_volcano("v1", "Renamed", 1.0, 1.0, 3, True, "D2")
    geojson = _geojson(adapter, date_codes=["D2"])
    feature = geojson["features"][0]
    assert feature["properties"]["vei"] == 3
    assert feature["properties"]["code"] == "D2"
    # name/lat/lon are NOT in the ON CONFLICT SET list, so they survive unchanged
    assert feature["properties"]["name"] == "Ruapehu"
    assert feature["geometry"]["coordinates"] == [175.57, -39.28]


def test_get_volcanoes_as_geojson_filters_by_vei_min():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("big", "Big", 0.0, 0.0, 4, True, "D1")
    adapter.update_volcano("small", "Small", 0.0, 0.0, 1, True, "D1")
    geojson = _geojson(adapter, vei_min=3, date_codes=["D1"])
    names = {f["properties"]["name"] for f in geojson["features"]}
    assert names == {"Big"}


def test_get_volcanoes_as_geojson_significant_filter():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("sig", "Sig", 0.0, 0.0, 2, True, "D1")
    adapter.update_volcano("nonsig", "NonSig", 0.0, 0.0, 2, False, "D1")

    # significant=False means "no filter" (matches everything)
    geojson_off = _geojson(adapter, significant=False, date_codes=["D1"])
    assert {f["properties"]["name"] for f in geojson_off["features"]} == {"Sig", "NonSig"}

    # significant=True means "only significant=True rows"
    geojson_on = _geojson(adapter, significant=True, date_codes=["D1"])
    assert {f["properties"]["name"] for f in geojson_on["features"]} == {"Sig"}


def test_get_volcanoes_as_geojson_filters_by_date_codes():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("d1", "D1Volcano", 0.0, 0.0, 2, True, "D1")
    adapter.update_volcano("d2", "D2Volcano", 0.0, 0.0, 2, True, "D2")
    geojson = _geojson(adapter, date_codes=["D1"])
    names = {f["properties"]["name"] for f in geojson["features"]}
    assert names == {"D1Volcano"}


def test_get_volcanoes_as_geojson_shape():
    adapter = FakeVolcanoAdapter()
    adapter.update_volcano("v1", "Ruapehu", -39.28, 175.57, 2, True, "D1")
    geojson = _geojson(adapter, date_codes=["D1"])
    feature = geojson["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [175.57, -39.28]
    assert feature["properties"] == {"name": "Ruapehu", "vei": 2, "code": "D1"}


def test_get_volcanoes_as_geojson_empty():
    adapter = FakeVolcanoAdapter()
    geojson = _geojson(adapter, date_codes=["D1"])
    assert geojson == {"type": "FeatureCollection", "features": []}
