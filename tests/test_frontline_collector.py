#!/usr/bin/env python3
"""FrontlineCollector: DeepState's raw map -> classified polygons, and the
fetch-only-new-updates flow, against FakeFrontlineAdapter (no network, no DB)."""
from unittest.mock import MagicMock

import pytest

from atmos_gl.collectors.frontline import (
    FrontlineCollector,
    frontline_features,
    latest_update,
    update_description,
    updates_to_store,
)
from atmos_gl.db.frontline_adapter import FakeFrontlineAdapter

_SQUARE = [[[37.12345678, 48.1, 0], [37.2, 48.1, 0], [37.2, 48.2, 0], [37.12345678, 48.1, 0]]]


def _feature(name, geometry_type="Polygon", coordinates=_SQUARE):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": geometry_type, "coordinates": coordinates}}


def test_areas_are_classified_by_their_geojson_tag():
    raw = {"type": "FeatureCollection", "features": [
        _feature("Окуповано /// Occupied /// geoJSON.status.occupied\n"),
        _feature("Статус невідомий /// Unknown status\xa0/// geoJSON.status.unknown"),
        _feature("Звільнено /// Liberated /// geoJSON.status.dismissed"),
        _feature("Звільнено 25.03 /// Liberated 25.03\xa0/// geoJSON.status.dismissed_at"),
        _feature("Окупований Крим /// Occupied Crimea\xa0/// geoJSON.territories.crimea"),
        _feature("ОРДЛО /// CADR and CALR\xa0/// geoJSON.territories.ordlo"),
    ]}
    statuses = [f["properties"] for f in frontline_features(raw)["features"]]
    assert statuses == [{"status": s} for s in
                        ["occupied", "contested", "liberated", "liberated", "occupied", "occupied"]]


def test_everything_else_is_dropped():
    raw = {"type": "FeatureCollection", "features": [
        # satirical / outside-Ukraine territories
        _feature("Тимчасово окупована східна Пруссія /// East Prussia /// geoJSON.territories.prussia"),
        _feature("Придністров'я/// Transnistria /// geoJSON.territories.transnistria"),
        # points: attack arrows carry no direction; units, airfields
        _feature("Напрямок удару /// Direction of attack /// geoJSON.status.attack_direction",
                 "Point", [36.8, 48.0, 0]),
        _feature("Окуповано /// Occupied /// geoJSON.status.occupied", "Point", [36.8, 48.0, 0]),
        _feature("328-й десантно-штурмовий полк /// 328th regiment", "Point", [36.8, 48.0, 0]),
        _feature("no tag at all"),
    ]}
    assert frontline_features(raw)["features"] == []


def test_coordinates_lose_altitude_and_excess_precision():
    geometry = frontline_features({"features": [_feature("x /// y /// geoJSON.status.occupied")]})[
        "features"][0]["geometry"]
    assert geometry["coordinates"][0][0] == [37.12346, 48.1]


def test_latest_update_is_the_newest_published_entry():
    history = [
        {"id": 1, "createdAt": "2026-09-30T10:00:00.000Z", "status": True},
        {"id": 3, "createdAt": "2026-10-02T10:00:00.000Z", "status": False},
        {"id": 2, "createdAt": "2026-10-01T18:23:57.000Z", "status": True},
    ]
    assert latest_update(history)["id"] == 2
    assert latest_update([]) is None


def test_description_prefers_english_and_strips_links():
    entry = {"description": "Ворог окупував Святопетрівку.",
             "descriptionEn": 'The enemy has occupied <a href="https://deepstatemap.live/en#x">Svyatopetrivka</a>.'}
    assert update_description(entry) == "The enemy has occupied Svyatopetrivka."
    assert update_description({"description": "Ворог окупував Святопетрівку.", "descriptionEn": ""}) \
        == "Ворог окупував Святопетрівку."


def _collector(history, raw_map):
    c = FrontlineCollector.__new__(FrontlineCollector)
    c.config = MagicMock()
    c.config.get_setting.side_effect = lambda s, k, d=None: (
        {"frontline": "https://deepstate.example/api"} if (s, k) == ("data_collector", "datasources") else d)
    c.settings = {}
    c.frontline_adapter = FakeFrontlineAdapter()
    fetched = []

    def fake_get(url, timeout=15, **kwargs):
        fetched.append(url)
        r = MagicMock()
        r.json.return_value = history if url.endswith("history/public") else raw_map
        return r

    c._get = fake_get
    return c, fetched


_HISTORY = [{"id": 1790879037, "createdAt": "2026-10-01T18:23:57.000Z", "status": True,
             "description": "uk", "descriptionEn": "The enemy has occupied Svyatopetrivka."}]
_RAW = {"features": [_feature("x /// Occupied /// geoJSON.status.occupied")]}


def test_collect_stores_a_new_update_then_skips_it():
    c, fetched = _collector(_HISTORY, _RAW)
    c.collect()
    assert fetched == ["https://deepstate.example/api/history/public",
                       "https://deepstate.example/api/history/1790879037/geojson"]
    assert c.frontline_adapter.has_snapshot(1790879037)

    fetched.clear()
    c.collect()
    assert fetched == ["https://deepstate.example/api/history/public"]


def test_collect_raises_when_an_update_has_no_areas():
    c, _ = _collector(_HISTORY, {"features": []})
    with pytest.raises(RuntimeError):
        c.collect()
    assert not c.frontline_adapter.has_snapshot(1790879037)


def _entry(snapshot_id, created_at, status=True):
    return {"id": snapshot_id, "createdAt": created_at, "status": status}


def test_baselines_are_the_updates_current_1_7_and_30_days_before_the_latest():
    history = [
        _entry(1, "2026-08-25T09:00:00.000Z"),
        _entry(2, "2026-09-01T09:00:00.000Z"),   # current 30 days before
        _entry(3, "2026-09-24T09:00:00.000Z"),   # current 7 days before
        _entry(4, "2026-09-30T08:00:00.000Z"),   # current 1 day before
        _entry(5, "2026-09-30T12:00:00.000Z"),   # < 1 day before: not a baseline
        _entry(6, "2026-10-01T09:00:00.000Z"),   # latest
    ]
    assert [e["id"] for e in updates_to_store(history)] == [6, 4, 3, 2]


def test_windows_sharing_one_update_store_it_once():
    history = [_entry(1, "2026-08-01T09:00:00.000Z"), _entry(2, "2026-10-01T09:00:00.000Z")]
    assert [e["id"] for e in updates_to_store(history)] == [2, 1]
    assert updates_to_store([]) == []


def test_collect_also_stores_the_baselines():
    history = [
        {**_entry(10, "2026-09-01T09:00:00.000Z"), "descriptionEn": "a"},
        {**_entry(11, "2026-10-01T09:00:00.000Z"), "descriptionEn": "b"},
    ]
    c, fetched = _collector(history, _RAW)
    c.collect()
    assert fetched == ["https://deepstate.example/api/history/public",
                       "https://deepstate.example/api/history/11/geojson",
                       "https://deepstate.example/api/history/10/geojson"]
    assert c.frontline_adapter.has_snapshot(10) and c.frontline_adapter.has_snapshot(11)
