#!/usr/bin/env python3
"""DeepStateMap.live -> frontline_snapshots: the Russo-Ukrainian front line as
DeepState publishes it (occupied, contested "grey zone" and liberated areas).

Two anonymous JSON endpoints under data_collector.datasources["frontline"] (the same
ones deepstatemap.live's own map loads):
  * history/public       -- every published update: id, createdAt, description(En)
  * history/<id>/geojson -- that update's whole map as one FeatureCollection

Each map feature's `name` is "<Ukrainian> /// <English> /// geoJSON.<tag>"; the tag is
what classifies it (_STATUS_BY_TAG). Only area polygons are kept: the map also carries
unit positions, airfields, attack-direction icons (points with no direction in the
data) and a handful of satirical "occupied" territories (East Prussia, Karelia, ...)
-- none of which this layer draws. Ukrainian-held territory isn't a polygon at all:
it's everything not occupied or contested.

Personal, non-commercial use only: DeepState's licence (deepstatemap.live/license-en.html)
makes the API free for volunteer/charitable use and forbids redistributing or proxying
it to third parties -- see the README's Frontline section.
"""
import logging
import re
from datetime import datetime

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.frontline_adapter import FrontlineAdapter
from atmos_gl.lib.text_sanitize import strip_html

logger = logging.getLogger(__name__)

# geoJSON.<tag> -> the layer's status. crimea/ordlo/tuzla are the pre-2022 occupied
# areas inside Ukraine, mapped by DeepState as named territories rather than as
# status.occupied; every other "territories" polygon lies outside Ukraine.
_STATUS_BY_TAG = {
    "status.occupied": "occupied",
    "territories.crimea": "occupied",
    "territories.ordlo": "occupied",
    "territories.tuzla": "occupied",
    "status.unknown": "contested",
    "status.dismissed": "liberated",
    "status.dismissed_at": "liberated",
}
_TAG_RE = re.compile(r"geoJSON\.([\w.]+)")
_LINK_TAG_RE = re.compile(r"</?a\b[^>]*>", re.I)
_AREA_TYPES = ("Polygon", "MultiPolygon")
_COORD_DECIMALS = 5  # ~1 m; DeepState's own 7 decimals only inflate the payload


def _status(feature: dict) -> str | None:
    match = _TAG_RE.search((feature.get("properties") or {}).get("name") or "")
    return _STATUS_BY_TAG.get(match.group(1)) if match else None


def _round_coords(coords):
    """Drops DeepState's always-zero altitude and trims precision, at any nesting."""
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], _COORD_DECIMALS), round(coords[1], _COORD_DECIMALS)]
    return [_round_coords(c) for c in coords]


def frontline_features(raw: dict) -> dict:
    """DeepState's raw FeatureCollection -> just the classified area polygons, each
    with a single property, status: occupied / contested / liberated."""
    features = []
    for f in raw.get("features") or []:
        geometry = f.get("geometry") or {}
        status = _status(f)
        if status is None or geometry.get("type") not in _AREA_TYPES:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": geometry["type"], "coordinates": _round_coords(geometry["coordinates"])},
            "properties": {"status": status},
        })
    return {"type": "FeatureCollection", "features": features}


def latest_update(history: list) -> dict | None:
    """The newest published entry of history/public, or None if there isn't one."""
    published = [h for h in history if h.get("status", True) and h.get("createdAt")]
    return max(published, key=lambda h: h["createdAt"], default=None)


def update_description(entry: dict) -> str | None:
    """The update's English note, falling back to the Ukrainian one; DeepState embeds
    <a> links to map coordinates, which the popup can't use, so tags are stripped."""
    text = entry.get("descriptionEn") or entry.get("description") or ""
    # Unwrap inline <a> links first so "<a>Name</a>." doesn't become "Name ."
    return strip_html(_LINK_TAG_RE.sub("", text)) or None


class FrontlineCollector(CollectorBase):
    section = "frontline"
    channel_key = "frontline"
    datasource_key = "frontline"

    def __init__(self, config):
        super().__init__(config)
        self.frontline_adapter = FrontlineAdapter()

    def _url(self, path: str) -> str:
        return f"{self.datasource_url('frontline').rstrip('/')}/{path}"

    def has_new_data(self) -> bool:
        changed = self._head_changed(self._url("history/public"))
        return True if changed is None else changed

    def collect(self) -> None:
        r = self._get(self._url("history/public"), timeout=30)
        if r is None:
            raise RuntimeError("Frontline: couldn't fetch DeepState's update history")
        entry = latest_update(r.json())
        if entry is None:
            logger.warning("Frontline: DeepState returned no published updates")
            return
        snapshot_id = int(entry["id"])
        if self.frontline_adapter.has_snapshot(snapshot_id):
            logger.debug(f"Frontline: update {snapshot_id} already stored")
            return

        g = self._get(self._url(f"history/{snapshot_id}/geojson"), timeout=60)
        if g is None:
            raise RuntimeError(f"Frontline: couldn't fetch DeepState update {snapshot_id}")
        geojson = frontline_features(g.json())
        if not geojson["features"]:
            raise RuntimeError(f"Frontline: update {snapshot_id} had no recognisable areas")
        self.frontline_adapter.save_snapshot(
            snapshot_id,
            datetime.fromisoformat(entry["createdAt"].replace("Z", "+00:00")),
            update_description(entry),
            geojson,
        )
        logger.info(f"Frontline: stored DeepState update {snapshot_id} ({len(geojson['features'])} areas)")
