#!/usr/bin/env python3
"""Markers sync: makes the DB 'markers' table consistent with the canonical
markers/markers.geojson.

This is not a remote data feed — it's a LOCAL FILE sync — but it lives under the
collectors umbrella so all DB-population work is driven from one place (data_collector),
on the same schedule machinery as the event feeds. The geojson remains the source of
truth for place/feature markers; edit it to add, move, or remove markers. Each run upserts
every feature and deletes any rows no longer present, so the table tracks the file. Static
columns are (re)written; the wx_* weather columns are left untouched (the markers task's
sampler owns those), so a re-sync never clobbers the last sampled weather.

`has_new_data()` uses the geojson's mtime as a cheap freshness signal (the local-file
analog of an ETag/HEAD check), so an unchanged file costs only a stat() each cycle.

Can also be run standalone for a one-off re-sync:
    python -m worldmap.collectors.markers_sync
"""
import os
import json
import logging

from .base import CollectorBase

logger = logging.getLogger(__name__)


def default_geojson_path():
    return os.path.join("markers", "markers.geojson")


def marker_id(name, lat, lon):
    """Stable key for a marker. Coordinates are fixed to 5 dp (~1 m) so the id is
    deterministic across syncs; moving a marker in the geojson yields a new id (old
    row pruned, new row inserted), which is the desired behaviour."""
    return f"{name}|{lat:.5f}|{lon:.5f}"


def load_marker_rows(geojson_path=None):
    """Parse the geojson into upsert-ready row dicts (ALL Point features — places AND
    marine 'feature' entries, since the frontend renders both)."""
    path = geojson_path or default_geojson_path()

    try:
        with open(path) as f:
            gj = json.load(f)
        rows = []
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            geom = feat.get("geometry", {}) or {}
            if geom.get("type") != "Point":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            name = props.get("name", "") or ""
            rows.append(
                {
                    "id": marker_id(name, lat, lon),
                    "name": name,
                    "kind": props.get("kind", "place") or "place",
                    "country": props.get("country"),
                    "priority": props.get("priority"),
                    "pop": props.get("pop"),
                    "capital": props.get("capital"),
                    "color": props.get("color"),
                    "timezone": props.get("timezone"),
                    "lat": lat,
                    "lon": lon,
                }
            )
        return rows
    except Exception as e:
        logger.error(f"Error loading markers GeoJSON data: {e}")
        return None


def import_markers(db, geojson_path=None):
    """Upsert all markers from the geojson and delete any rows no longer present.
    Returns {"upserted": n, "deleted": n}. If the file yields no rows (missing/empty/
    unreadable), the delete is SKIPPED so a bad read can't wipe the table."""
    path = geojson_path or default_geojson_path()
    try:
        rows = load_marker_rows(path)
    except Exception as e:
        logger.error(f"Markers sync: could not read {path}: {e}")
        return {"upserted": 0, "deleted": 0}

    if not rows:
        logger.warning(
            f"Markers sync: no features in {path}; skipping sync to avoid wiping "
            "the table."
        )
        return {"upserted": 0, "deleted": 0}

    db.upsert_markers(rows)
    deleted = db.delete_markers_not_in([r["id"] for r in rows])
    logger.info(
        f"Markers sync: upserted {len(rows)}, deleted {deleted} "
        f"(from {os.path.basename(path)})"
    )
    return {"upserted": len(rows), "deleted": deleted}


class MarkersSyncCollector(CollectorBase):
    """Periodic local-file sync of markers.geojson into the DB markers table.

    Runs under collect_event_feeds() like the other collectors, so it honours the
    `markers` section's runs_per_day cadence and (like all collectors) runs regardless
    of the layer's `enabled` flag — the marker rows must be in the DB ready for the
    frontend to render the moment the layer is toggled on.
    """

    section = "markers"

    def _geojson_path(self):
        return self.settings.get("infile") or default_geojson_path()

    def has_new_data(self) -> bool:
        """Cheap freshness check: re-sync only when the geojson's mtime has changed."""
        path = self._geojson_path()
        try:
            mtime = str(os.path.getmtime(path))
        except OSError:
            # Can't stat (missing/unreadable) — let collect() run; import_markers()
            # handles a bad read safely (it won't wipe the table).
            return True
        # Note: the mtime cache is updated in collect() only on a successful import, so a
        # transient empty/unreadable file is retried rather than being marked "seen".
        return self._etag_cache.get(path) != mtime

    def collect(self) -> None:
        path = self._geojson_path()
        result = import_markers(self.db, geojson_path=path)
        if result.get("upserted", 0) > 0:
            try:
                self._etag_cache[path] = str(os.path.getmtime(path))
            except OSError:
                pass


def main():
    logging.basicConfig(level=logging.INFO)
    from worldmap.lib.db import Database

    import_markers(Database())


if __name__ == "__main__":
    main()
