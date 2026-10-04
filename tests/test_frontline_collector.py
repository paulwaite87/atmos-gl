#!/usr/bin/env python3
"""FrontlineCollector: DeepState's raw map -> classified polygons, and the
fetch-only-new-updates flow, against FakeFrontlineAdapter (no network, no DB)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from atmos_gl.collectors.frontline import (
    FrontlineCollector,
    area_note,
    description_segments,
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
    statuses = [f["properties"]["status"] for f in frontline_features(raw)["features"]]
    assert statuses == ["occupied", "contested", "liberated", "liberated", "occupied", "occupied"]


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


def _liberated(name, description=None):
    f = _feature(name)
    if description is not None:
        f["properties"]["description"] = description
    return f


def test_liberated_areas_carry_their_date_and_english_note():
    raw = {"features": [
        _liberated("Звільнено 27-29.03 /// Liberated 27-29.03 /// geoJSON.status.dismissed_at {{at:27.03 - 29.03}}",
                   "Підтверджено волонтерами<br>///<br>Confirmed first by volunteers, then by the General Staff<br>/// geoJSON.descriptions.#1"),
        _liberated("Звільнено /// Liberated /// geoJSON.status.dismissed"),
        _feature("x /// Occupied /// geoJSON.status.occupied"),
    ]}
    props = [f["properties"] for f in frontline_features(raw)["features"]]
    assert props == [
        {"status": "liberated", "liberated_on": "27.03–29.03",
         "note": "Confirmed first by volunteers, then by the General Staff"},
        {"status": "liberated", "liberated_on": None, "note": None},
        {"status": "occupied"},
    ]


def test_area_note_drops_the_repeated_url_fragment_and_falls_back_to_ukrainian():
    assert area_note(
        "На каналі є детальний пост https://t.me/DeepStateUA/10772<br>/// In our Telegram there is "
        "a separate message about this https://t.me/DeepStateUA/10772 /// "
        "(https://t.me/DeepStateUA/10772 ///) geoJSON.descriptions.#2"
    ) == "In our Telegram there is a separate message about this https://t.me/DeepStateUA/10772"
    assert area_note(
        "На каналі є пост https://t.me/DeepStateUA/10772<br><br>/// In our Telegram there is a separate "
        "message about this https://t.me/DeepStateUA/10772 (https://t.me/DeepStateUA/10772 )<br>"
        "/// geoJSON.descriptions.#2"
    ) == "In our Telegram there is a separate message about this https://t.me/DeepStateUA/10772"
    assert area_note("See (https://t.me/x/1 ) only once") == "See (https://t.me/x/1 ) only once"
    assert area_note("Тільки українською") == "Тільки українською"
    assert area_note(None) is None


def test_description_links_become_fly_to_and_external_segments():
    entry = {"descriptionEn": (
        'The enemy has occupied <a href="https://deepstatemap.live/en#dl!coords!47.69243236930309,36.164073944091804">'
        'Svyatopetrivka</a> and advanced near <a href="https://deepstatemap.live/en#14/47.6780851/ 36.1425371">'
        'Staroukrainka</a>, near <a href="https://deepstatemap.live/en#dl!city!dsm:l:123">Hulyaipole</a>.'
        ' Details: <a href="https://t.me/DeepStateEN/99">Telegram</a>'
    )}
    assert description_segments(entry) == [
        {"text": "The enemy has occupied "},
        {"text": "Svyatopetrivka", "lat": 47.69243236930309, "lon": 36.164073944091804, "zoom": 13},
        {"text": " and advanced near "},
        {"text": "Staroukrainka", "lat": 47.6780851, "lon": 36.1425371, "zoom": 14.0},
        {"text": ", near Hulyaipole. Details: "},
        {"text": "Telegram", "url": "https://t.me/DeepStateEN/99"},
    ]
    assert update_description(entry) == (
        "The enemy has occupied Svyatopetrivka and advanced near Staroukrainka, near Hulyaipole. Details: Telegram")


def test_out_of_range_or_foreign_map_links_stay_plain_text():
    entry = {"descriptionEn": '<a href="https://deepstatemap.live/#14/95.0/36.1">Nowhere</a> '
                              '<a href="javascript:alert(1)">x</a>'}
    assert description_segments(entry) == [{"text": "Nowhere x"}]
    assert description_segments({"descriptionEn": "", "description": ""}) is None


def test_collect_stores_the_description_segments():
    c, _ = _collector(_HISTORY, _RAW)
    c.collect()
    stored = c.frontline_adapter.get_snapshot_at()
    assert stored["description_segments"] == [{"text": "The enemy has occupied Svyatopetrivka."}]


def _arrow(n, coords=(36.82590, 48.06571, 0)):
    f = _feature("Напрямок удару /// Direction of attack /// geoJSON.status.attack_direction\n",
                 "Point", list(coords))
    f["properties"]["description"] = f"{{icon=arrow_{n}}}"
    return f


def test_attack_arrows_keep_their_point_and_compass_bearing():
    raw = {"features": [_arrow(4), _arrow(8), _arrow(12), _arrow(16), _arrow(1)]}
    features = frontline_features(raw)["features"]
    assert [f["properties"] for f in features] == [
        {"status": "attack_direction", "bearing": b} for b in (90.0, 180.0, 270.0, 0.0, 22.5)
    ]
    assert features[0]["geometry"] == {"type": "Point", "coordinates": [36.8259, 48.06571]}


def test_attack_points_without_a_known_arrow_are_dropped():
    no_icon = _arrow(1)
    no_icon["properties"]["description"] = None
    raw = {"features": [_arrow(17), _arrow(0), no_icon]}
    assert frontline_features(raw)["features"] == []


def _versioned_history(updated_at):
    return [{"id": 1790879037, "createdAt": "2026-10-01T18:23:57.000Z", "updatedAt": updated_at,
             "status": True, "descriptionEn": "The enemy has occupied Svyatopetrivka."}]


def test_an_update_deepstate_edits_is_fetched_again():
    c, fetched = _collector(_versioned_history("2026-10-01T18:24:00.000Z"), _RAW)
    c.collect()
    fetched.clear()

    c.collect()  # unchanged updatedAt: list only
    assert fetched == ["https://deepstate.example/api/history/public"]

    edited = _versioned_history("2026-10-02T14:35:27.231Z")
    c._get = _collector(edited, {"features": [_feature("x /// y /// geoJSON.status.unknown")]})[0]._get
    c.collect()
    stored = c.frontline_adapter.get_snapshot_at()
    assert [f["properties"]["status"] for f in stored["geojson"]["features"]] == ["contested"]
    assert stored["source_updated_at"].isoformat() == "2026-10-02T14:35:27.231000+00:00"


def test_snapshots_stored_before_versioning_are_refetched_once():
    c, fetched = _collector(_versioned_history("2026-10-02T14:35:27.231Z"), _RAW)
    c.frontline_adapter.save_snapshot(  # as stored by an older collector: no updatedAt
        1790879037, datetime(2026, 10, 1, 18, 23, 57, tzinfo=timezone.utc), "old", _RAW)
    c.collect()
    assert fetched[-1].endswith("history/1790879037/geojson")
    fetched.clear()
    c.collect()
    assert fetched == ["https://deepstate.example/api/history/public"]
