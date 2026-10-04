#!/usr/bin/env python3
"""DeepStateMap.live -> frontline_snapshots: the Russo-Ukrainian front line as
DeepState publishes it (occupied, contested "grey zone" and liberated areas).

Two anonymous JSON endpoints under data_collector.datasources["frontline"] (the same
ones deepstatemap.live's own map loads):
  * history/public       -- every published update: id, createdAt, description(En)
  * history/<id>/geojson -- that update's whole map as one FeatureCollection

Each map feature's `name` is "<Ukrainian> /// <English> /// geoJSON.<tag>"; the tag is
what classifies it (_STATUS_BY_TAG). Kept: the area polygons, and the "direction of
attack" points -- each one's heading is the pre-rotated icon its description names,
"{icon=arrow_N}" (_attack_bearing). Dropped: unit positions, airfields and a handful of
satirical "occupied" territories (East Prussia, Karelia, ...). Ukrainian-held territory isn't a polygon at all:
it's everything not occupied or contested.

Personal, non-commercial use only: DeepState's licence (deepstatemap.live/license-en.html)
makes the API free for volunteer/charitable use and forbids redistributing or proxying
it to third parties -- see the README's Frontline section.
"""
import html
import logging
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.frontline_adapter import FrontlineAdapter
from atmos_gl.lib.frontline_changes import CHANGE_WINDOWS_DAYS
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
# A liberated area's date(s), e.g. "{{at:27.03 - 29.03}}" -- day.month, no year.
_LIBERATED_AT_RE = re.compile(r"\{\{at:([^}]*)\}\}")
# DeepState's own translation-key suffix on area descriptions: "geoJSON.descriptions.#7"
_DESCRIPTION_KEY_RE = re.compile(r"geoJSON\.descriptions\.#\d+")
_NOTE_MAX = 400
# DeepState's own markup repeats a note's link as "(<url> )"; dropped when the same URL
# already appears in the note.
_PAREN_URL_RE = re.compile(r"\s*\(\s*(https?://[^\s()]+)\s*\)")
# Coordinates in a deepstatemap.live link's fragment: "#<zoom>/<lat>/<lon>" (sometimes
# with stray spaces) or "#dl!coords!<lat>,<lon>". "#dl!city!<id>" links carry no
# coordinates and stay plain text.
_ZOOM_LAT_LON_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*$")
_COORDS_RE = re.compile(r"^dl!coords!\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_DEFAULT_FLY_ZOOM = 13
_AREA_TYPES = ("Polygon", "MultiPolygon")
_ATTACK_TAG = "status.attack_direction"
ATTACK_DIRECTION = "attack_direction"
# deepstatemap.live draws "{icon=arrow_N}" with /images/custom/arrow_N.png -- 16
# pre-rotated arrows, checked by eye: arrow_N points N x 22.5 degrees clockwise from
# north (arrow_4 east, arrow_8 south, arrow_12 west, arrow_16 north), drawn centred
# on the point.
_ARROW_ICON_RE = re.compile(r"\{icon=arrow_(\d+)\b")
_ARROW_DIRECTIONS = 16
_COORD_DECIMALS = 5  # ~1 m; DeepState's own 7 decimals only inflate the payload


def _tag(feature: dict) -> str | None:
    match = _TAG_RE.search((feature.get("properties") or {}).get("name") or "")
    return match.group(1) if match else None


def _status(feature: dict) -> str | None:
    return _STATUS_BY_TAG.get(_tag(feature))


def _attack_bearing(feature: dict) -> float | None:
    """Compass bearing (degrees clockwise from north) of a direction-of-attack point,
    from its "{icon=arrow_N}" description; None if it names no known arrow."""
    match = _ARROW_ICON_RE.search(str((feature.get("properties") or {}).get("description") or ""))
    if not match or not 1 <= int(match.group(1)) <= _ARROW_DIRECTIONS:
        return None
    return int(match.group(1)) * 360 / _ARROW_DIRECTIONS % 360


def _liberated_on(name: str) -> str | None:
    """"27.03 - 29.03" -> "27.03–29.03"; None for an undated liberated area."""
    match = _LIBERATED_AT_RE.search(name)
    if not match or not match.group(1).strip():
        return None
    return re.sub(r"\s*-\s*", "–", match.group(1).strip())


def area_note(description: str | None) -> str | None:
    """A liberated area's note in English where DeepState gives one. The raw form is
    "<Ukrainian> /// <English> /// geoJSON.descriptions.#N" (with <br>s, and sometimes a
    repeated "(url ///)" fragment), so the second part wins, falling back to the first."""
    if not description:
        return None
    parts = [
        strip_html(_DESCRIPTION_KEY_RE.sub("", html.unescape(part))) or ""
        for part in description.split("///")
    ]
    note = (parts[1] if len(parts) > 1 and parts[1] else parts[0]) or None
    if note:
        note = _PAREN_URL_RE.sub(
            lambda m: "" if note.count(m.group(1)) > 1 else m.group(0), note
        ).strip() or None
    if note and len(note) > _NOTE_MAX:
        note = note[:_NOTE_MAX].rsplit(" ", 1)[0] + "…"
    return note


def _round_coords(coords):
    """Drops DeepState's always-zero altitude and trims precision, at any nesting."""
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], _COORD_DECIMALS), round(coords[1], _COORD_DECIMALS)]
    return [_round_coords(c) for c in coords]


def frontline_features(raw: dict) -> dict:
    """DeepState's raw FeatureCollection -> the classified area polygons (status:
    occupied / contested / liberated) and direction-of-attack points (status:
    attack_direction, with bearing)."""
    features = []
    for f in raw.get("features") or []:
        geometry = f.get("geometry") or {}
        if _tag(f) == _ATTACK_TAG and geometry.get("type") == "Point":
            bearing = _attack_bearing(f)
            if bearing is not None:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": _round_coords(geometry["coordinates"])},
                    "properties": {"status": ATTACK_DIRECTION, "bearing": bearing},
                })
            continue
        status = _status(f)
        if status is None or geometry.get("type") not in _AREA_TYPES:
            continue
        properties = {"status": status}
        if status == "liberated":
            raw = f.get("properties") or {}
            properties["liberated_on"] = _liberated_on(raw.get("name") or "")
            properties["note"] = area_note(raw.get("description"))
        features.append({
            "type": "Feature",
            "geometry": {"type": geometry["type"], "coordinates": _round_coords(geometry["coordinates"])},
            "properties": properties,
        })
    return {"type": "FeatureCollection", "features": features}


def latest_update(history: list) -> dict | None:
    """The newest published entry of history/public, or None if there isn't one."""
    published = [h for h in history if h.get("status", True) and h.get("createdAt")]
    return max(published, key=lambda h: h["createdAt"], default=None)


def created_at(entry: dict) -> datetime:
    return datetime.fromisoformat(entry["createdAt"].replace("Z", "+00:00"))


def source_updated_at(entry: dict) -> datetime | None:
    """DeepState's updatedAt for an update -- bumped whenever it edits a published one
    (seen live: an update revised the day after it went up)."""
    value = entry.get("updatedAt")
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def updates_to_store(history: list) -> list:
    """The latest published update, then the one current CHANGE_WINDOWS_DAYS before
    it, deduplicated (a quiet week can make two windows share one update) -- newest
    first. Empty if nothing is published."""
    latest = latest_update(history)
    if latest is None:
        return []
    wanted = [latest]
    for days in CHANGE_WINDOWS_DAYS:
        cutoff = created_at(latest) - timedelta(days=days)
        baseline = latest_update([h for h in history if h.get("createdAt") and created_at(h) <= cutoff])
        if baseline is not None and all(baseline["id"] != w["id"] for w in wanted):
            wanted.append(baseline)
    return wanted


def _fly_target(href: str) -> dict | None:
    """{lat, lon, zoom} for a deepstatemap.live map link, else None."""
    parts = urlsplit(href.strip())
    if parts.netloc and not parts.netloc.endswith("deepstatemap.live"):
        return None
    if (m := _ZOOM_LAT_LON_RE.match(parts.fragment)):
        zoom, lat, lon = float(m.group(1)), float(m.group(2)), float(m.group(3))
    elif (m := _COORDS_RE.match(parts.fragment)):
        zoom, lat, lon = _DEFAULT_FLY_ZOOM, float(m.group(1)), float(m.group(2))
    else:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"lat": lat, "lon": lon, "zoom": min(zoom, 18)}


def _external_url(href: str) -> str | None:
    parts = urlsplit(href.strip())
    if parts.scheme in ("http", "https") and parts.netloc and not parts.netloc.endswith("deepstatemap.live"):
        return href.strip()
    return None


class _SegmentParser(HTMLParser):
    """Splits a description's HTML into text and link segments; every other tag is
    dropped (a <br> becomes a space)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.segments: list[dict] = []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
        elif tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        segment = {"text": data}
        if self._href is not None:
            if (target := _fly_target(self._href)):
                segment.update(target)
            elif (url := _external_url(self._href)):
                segment["url"] = url
        last = self.segments[-1] if self.segments else None
        if last is not None and set(last) == {"text"} and set(segment) == {"text"}:
            last["text"] += data
        else:
            self.segments.append(segment)


def description_segments(entry: dict) -> list[dict] | None:
    """The update's note (English, falling back to Ukrainian) as an ordered list of
    {"text"} pieces, where a piece linked to a deepstatemap.live map position also
    carries {"lat", "lon", "zoom"} (the popup turns it into a fly-to link) and one
    linked elsewhere (e.g. DeepState's Telegram) carries {"url"}. Whitespace is
    collapsed; None when there's no note."""
    raw = entry.get("descriptionEn") or entry.get("description") or ""
    parser = _SegmentParser()
    parser.feed(raw)
    parser.close()
    segments = []
    for segment in parser.segments:
        text = re.sub(r"\s+", " ", segment["text"])
        if text:
            segments.append({**segment, "text": text})
    if segments:
        segments[0]["text"] = segments[0]["text"].lstrip()
        segments[-1]["text"] = segments[-1]["text"].rstrip()
    segments = [s for s in segments if s["text"]]
    return segments or None


def update_description(entry: dict) -> str | None:
    """The update's note as plain text (description_segments() joined)."""
    segments = description_segments(entry)
    return "".join(s["text"] for s in segments) if segments else None


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
        """Stores DeepState's latest update, plus the update that was current 1/7/30
        days before it (CHANGE_WINDOWS_DAYS) -- the baselines the gains/losses view
        compares against. Each is fetched once, then again only when DeepState's
        updatedAt for it moves on from the stored one (it edits published updates)."""
        r = self._get(self._url("history/public"), timeout=30)
        if r is None:
            raise RuntimeError("Frontline: couldn't fetch DeepState's update history")
        history = r.json()
        for entry in updates_to_store(history):
            snapshot_id = int(entry["id"])
            if not self.frontline_adapter.has_snapshot(snapshot_id):
                self._store(entry)
                continue
            edited = source_updated_at(entry)
            stored = self.frontline_adapter.get_source_updated_at(snapshot_id)
            if edited is not None and edited != stored:
                reason = "edited by DeepState" if stored else "stored without a version"
                logger.info(f"Frontline: update {snapshot_id} {reason}; re-fetching")
                self._store(entry)

    def _store(self, entry: dict) -> None:
        snapshot_id = int(entry["id"])
        g = self._get(self._url(f"history/{snapshot_id}/geojson"), timeout=60)
        if g is None:
            raise RuntimeError(f"Frontline: couldn't fetch DeepState update {snapshot_id}")
        geojson = frontline_features(g.json())
        if not geojson["features"]:
            raise RuntimeError(f"Frontline: update {snapshot_id} had no recognisable areas")
        self.frontline_adapter.save_snapshot(
            snapshot_id, created_at(entry), update_description(entry), geojson,
            description_segments=description_segments(entry),
            source_updated_at=source_updated_at(entry),
        )
        arrows = sum(1 for f in geojson["features"] if f["properties"]["status"] == ATTACK_DIRECTION)
        logger.info(
            f"Frontline: stored DeepState update {snapshot_id} "
            f"({len(geojson['features']) - arrows} areas, {arrows} attack arrows)"
        )
