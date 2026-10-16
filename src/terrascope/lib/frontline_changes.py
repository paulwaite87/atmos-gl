#!/usr/bin/env python3
"""Gains and losses between two DeepState front-line snapshots (see
collectors/frontline.py) -- "occupied" areas only: a Russian gain is land occupied in
the newer snapshot but not the older one, a Ukrainian gain the reverse. Contested (grey
zone) changes are deliberately ignored; DeepState's own daily area figures count
occupied territory the same way.

Pure geometry (shapely), no DB: FrontlineAdapter/FakeFrontlineAdapter only return the
two snapshots, so the diff itself is written once rather than per adapter.
"""
from pyproj import Geod
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

# The comparison windows the view offers, each measured back from DeepState's latest
# update (not from now -- DeepState doesn't post every day, and "the last day" should
# still show its latest change). The collector stores a baseline for each.
CHANGE_WINDOWS_DAYS = (1, 7, 30)

RUSSIAN_GAIN = "russian_gain"
UKRAINIAN_GAIN = "ukrainian_gain"

# Redrawing a shared border leaves hairline slivers in a polygon difference; checked
# live against 1/7/30-day windows, real changes are all well above this.
MIN_AREA_KM2 = 0.05

_GEOD = Geod(ellps="WGS84")


def _occupied(geojson: dict):
    return unary_union([
        make_valid(shape(f["geometry"]))
        for f in geojson.get("features") or []
        if (f.get("properties") or {}).get("status") == "occupied"
    ])


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [p for g in getattr(geometry, "geoms", []) for p in _polygons(g)]


def area_km2(polygon) -> float:
    return abs(_GEOD.geometry_area_perimeter(polygon)[0]) / 1e6


def occupied_changes(older: dict, newer: dict) -> tuple[list, dict]:
    """(features, totals): one Polygon feature per changed area, properties
    {"change": russian_gain|ukrainian_gain, "area_km2"}, largest first; totals maps
    each change to its summed km². Areas under MIN_AREA_KM2 are dropped."""
    before, after = _occupied(older), _occupied(newer)
    features = []
    totals = {RUSSIAN_GAIN: 0.0, UKRAINIAN_GAIN: 0.0}
    for change, diff in ((RUSSIAN_GAIN, after.difference(before)),
                         (UKRAINIAN_GAIN, before.difference(after))):
        for polygon in _polygons(diff):
            area = area_km2(polygon)
            if area < MIN_AREA_KM2:
                continue
            totals[change] += area
            features.append({
                "type": "Feature",
                "geometry": mapping(polygon),
                "properties": {"change": change, "area_km2": round(area, 2)},
            })
    features.sort(key=lambda f: -f["properties"]["area_km2"])
    return features, {k: round(v, 1) for k, v in totals.items()}


def _stamp(snapshot: dict) -> dict:
    return {"id": snapshot["id"], "created_at": snapshot["created_at"].isoformat()}


def changes_feature_collection(latest: dict | None, baseline: dict | None, days: int) -> dict:
    """GET /api/frontline/changes's body: the changed areas between two
    FrontlineAdapter.get_snapshot_at() snapshots, plus a "comparison" foreign member
    (which updates were compared, km² totals). comparison is None until both exist --
    a fresh deployment, before the collector has stored the window's baseline."""
    if latest is None or baseline is None:
        return {"type": "FeatureCollection", "features": [], "comparison": None}
    features, totals = (
        occupied_changes(baseline["geojson"], latest["geojson"])
        if baseline["id"] != latest["id"] else ([], {RUSSIAN_GAIN: 0.0, UKRAINIAN_GAIN: 0.0})
    )
    return {
        "type": "FeatureCollection",
        "features": features,
        "comparison": {
            "days": days, "from": _stamp(baseline), "to": _stamp(latest), "totals_km2": totals,
        },
    }
