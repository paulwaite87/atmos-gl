#!/usr/bin/env python3
"""Markers importer: makes the DB 'markers' table consistent with the canonical
src/atmos_gl/markers/markers.geojson.

The geojson remains the source of truth for the place/feature markers themselves — edit
it to add, move, or remove markers. Each cycle the markers task runs this importer, which
upserts every feature and deletes any rows no longer present, so the table tracks the
file. Static columns are (re)written; the wx_* weather columns are left untouched here
(the sampler owns those), so a re-import never clobbers the last sampled weather.

Can also be run standalone for a one-off re-sync:
    python -m atmos_gl.markers_importer
"""
import os
import json
import logging

logger = logging.getLogger(__name__)


def default_geojson_path():
    return os.path.join("markers", "markers.geojson")


def marker_id(name, lat, lon):
    """Stable key for a marker. Coordinates are fixed to 5 dp (~1 m) so the id is
    deterministic across imports; moving a marker in the geojson yields a new id (old
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


def import_markers(marker_adapter, geojson_path=None):
    """Upsert all markers from the geojson and delete any rows no longer present.
    Returns {"upserted": n, "deleted": n}. If the file yields no rows (missing/empty/
    unreadable), the delete is SKIPPED so a bad read can't wipe the table."""
    path = geojson_path or default_geojson_path()
    try:
        rows = load_marker_rows(path)
    except Exception as e:
        logger.error(f"Markers importer: could not read {path}: {e}")
        return {"upserted": 0, "deleted": 0}

    if not rows:
        logger.warning(
            f"Markers importer: no features in {path}; skipping sync to avoid wiping "
            "the table."
        )
        return {"upserted": 0, "deleted": 0}

    marker_adapter.upsert_markers(rows)
    deleted = marker_adapter.delete_markers_not_in([r["id"] for r in rows])
    logger.info(
        f"Markers importer: upserted {len(rows)}, deleted {deleted} "
        f"(from {os.path.basename(path)})"
    )
    return {"upserted": len(rows), "deleted": deleted}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from atmos_gl.db.marker_adapter import MarkerAdapter

    import_markers(MarkerAdapter())
