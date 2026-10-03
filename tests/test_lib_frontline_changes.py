#!/usr/bin/env python3
"""Occupied-area gains/losses between two front-line snapshots (pure shapely)."""
from datetime import datetime, timezone

import pytest

from atmos_gl.lib.frontline_changes import (
    MIN_AREA_KM2,
    changes_feature_collection,
    occupied_changes,
)


def _box(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def _fc(*areas):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"status": status},
         "geometry": {"type": "Polygon", "coordinates": coords}}
        for status, coords in areas
    ]}


def test_newly_occupied_land_is_a_russian_gain_and_lost_land_a_ukrainian_gain():
    older = _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2)))
    # pushed east by 0.1 deg, lost the westmost 0.05 deg
    newer = _fc(("occupied", _box(37.05, 48.0, 37.3, 48.2)))

    features, totals = occupied_changes(older, newer)

    changes = {f["properties"]["change"]: f["properties"]["area_km2"] for f in features}
    assert set(changes) == {"russian_gain", "ukrainian_gain"}
    # 0.1 x 0.2 deg vs 0.05 x 0.2 deg at ~48N: roughly 2:1
    assert changes["russian_gain"] == pytest.approx(2 * changes["ukrainian_gain"], rel=0.01)
    assert totals == {"russian_gain": round(changes["russian_gain"], 1),
                      "ukrainian_gain": round(changes["ukrainian_gain"], 1)}
    assert features[0]["properties"]["change"] == "russian_gain"  # largest first


def test_grey_zone_changes_are_not_gains():
    older = _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2)))
    newer = _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2)), ("contested", _box(37.2, 48.0, 37.4, 48.2)))
    assert occupied_changes(older, newer) == ([], {"russian_gain": 0.0, "ukrainian_gain": 0.0})


def test_redrawn_borders_leave_no_slivers():
    older = _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2)))
    newer = _fc(("occupied", _box(37.0, 48.0, 37.20001, 48.2)))  # ~1 m wider
    features, _ = occupied_changes(older, newer)
    assert features == []
    assert MIN_AREA_KM2 > 0


def _snap(snapshot_id, day, geojson):
    return {"id": snapshot_id, "created_at": datetime(2026, 10, day, tzinfo=timezone.utc),
            "description": None, "geojson": geojson}


def test_feature_collection_carries_the_comparison():
    older = _snap(1, 1, _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2))))
    newer = _snap(2, 8, _fc(("occupied", _box(37.0, 48.0, 37.3, 48.2))))
    body = changes_feature_collection(newer, older, 7)
    assert [f["properties"]["change"] for f in body["features"]] == ["russian_gain"]
    assert body["comparison"]["days"] == 7
    assert body["comparison"]["from"] == {"id": 1, "created_at": "2026-10-01T00:00:00+00:00"}
    assert body["comparison"]["to"]["id"] == 2
    assert body["comparison"]["totals_km2"]["ukrainian_gain"] == 0.0


def test_no_comparison_until_both_snapshots_exist():
    newer = _snap(2, 8, _fc())
    assert changes_feature_collection(newer, None, 7)["comparison"] is None
    assert changes_feature_collection(None, None, 7) == {
        "type": "FeatureCollection", "features": [], "comparison": None}


def test_the_same_snapshot_on_both_sides_means_no_change():
    snap = _snap(2, 8, _fc(("occupied", _box(37.0, 48.0, 37.2, 48.2))))
    body = changes_feature_collection(snap, snap, 1)
    assert body["features"] == []
    assert body["comparison"]["totals_km2"] == {"russian_gain": 0.0, "ukrainian_gain": 0.0}
