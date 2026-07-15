import logging

from sqlalchemy import bindparam, cast, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import Marker

logger = logging.getLogger(__name__)


class MarkerAdapter:
    """Real adapter for markers, backed by SQLAlchemy."""

    def upsert_markers(self, rows):
        """Bulk-upsert marker STATIC fields from the geojson. Each row is a dict with
        keys: id, name, kind, country, priority, pop, capital, color, timezone, lat, lon.
        Deliberately does NOT touch the wx_* columns, so a re-import preserves the last
        sampled weather."""
        if not rows:
            return
        values = [
            {
                **r,
                "geom": f"SRID=4326;POINT({r['lon']} {r['lat']})",
            }
            for r in rows
        ]
        stmt = pg_insert(Marker)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Marker.id],
            set_={
                "name": stmt.excluded.name,
                "kind": stmt.excluded.kind,
                "country": stmt.excluded.country,
                "priority": stmt.excluded.priority,
                "pop": stmt.excluded.pop,
                "capital": stmt.excluded.capital,
                "color": stmt.excluded.color,
                "timezone": stmt.excluded.timezone,
                "lat": stmt.excluded.lat,
                "lon": stmt.excluded.lon,
                "geom": stmt.excluded.geom,
            },
        )
        with Session() as session:
            session.execute(stmt, values)
            session.commit()

    def delete_markers_not_in(self, ids):
        """Delete markers whose id is NOT in `ids` (i.e. removed from the geojson).
        Returns the number of rows deleted. Guarded by the caller against an empty list
        so a failed geojson read can't wipe the table."""
        if not ids:
            return 0
        with Session() as session:
            result = session.execute(delete(Marker).where(Marker.id.notin_(list(ids))))
            session.commit()
            return result.rowcount

    def update_marker_weather(self, updates):
        """Bulk-update the wx_* weather columns. Each update is a dict with keys:
        id, t (deg C), rh (%), ws (m/s), wd (deg from), valid_time (ISO str or None).
        Rows not matched (id absent) are simply no-ops."""
        if not updates:
            return
        stmt = (
            Marker.__table__.update()
            .where(Marker.id == bindparam("m_id"))
            .values(
                wx_temp_c=bindparam("t"),
                wx_humidity_pct=bindparam("rh"),
                wx_wind_ms=bindparam("ws"),
                wx_wind_dir_deg=bindparam("wd"),
                wx_valid_time=bindparam("valid_time"),
                wx_updated_at=func.now(),
            )
        )
        params = [{**u, "m_id": u["id"]} for u in updates]
        with Session() as session:
            session.execute(stmt, params)
            session.commit()

    def get_markers_as_geojson(self):
        """All markers as a GeoJSON FeatureCollection, static fields + current weather
        folded into properties (weather keys are null where not yet sampled)."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Marker.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "name",
                Marker.name,
                "kind",
                Marker.kind,
                "country",
                Marker.country,
                "priority",
                Marker.priority,
                "pop",
                Marker.pop,
                "capital",
                Marker.capital,
                "color",
                Marker.color,
                "timezone",
                Marker.timezone,
                "t",
                Marker.wx_temp_c,
                "rh",
                Marker.wx_humidity_pct,
                "ws",
                Marker.wx_wind_ms,
                "wd",
                Marker.wx_wind_dir_deg,
                "wx_valid_time",
                Marker.wx_valid_time,
            ),
        )
        collection = as_feature_collection(feature)
        stmt = select(cast(collection, SqlText)).select_from(Marker)
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building markers GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION


class FakeMarkerAdapter:
    """In-memory fake for markers, matching MarkerAdapter's method contracts."""

    def __init__(self):
        self._markers: dict[str, dict] = {}

    def upsert_markers(self, rows):
        if not rows:
            return
        for r in rows:
            existing = self._markers.get(r["id"], {})
            existing.update(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "country": r["country"],
                    "priority": r["priority"],
                    "pop": r["pop"],
                    "capital": r["capital"],
                    "color": r["color"],
                    "timezone": r["timezone"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                }
            )
            existing.setdefault("wx_temp_c", None)
            existing.setdefault("wx_humidity_pct", None)
            existing.setdefault("wx_wind_ms", None)
            existing.setdefault("wx_wind_dir_deg", None)
            existing.setdefault("wx_valid_time", None)
            self._markers[r["id"]] = existing

    def delete_markers_not_in(self, ids):
        if not ids:
            return 0
        ids = set(ids)
        to_delete = [mid for mid in self._markers if mid not in ids]
        for mid in to_delete:
            del self._markers[mid]
        return len(to_delete)

    def update_marker_weather(self, updates):
        if not updates:
            return
        for u in updates:
            marker = self._markers.get(u["id"])
            if marker is None:
                continue
            marker["wx_temp_c"] = u["t"]
            marker["wx_humidity_pct"] = u["rh"]
            marker["wx_wind_ms"] = u["ws"]
            marker["wx_wind_dir_deg"] = u["wd"]
            marker["wx_valid_time"] = u["valid_time"]

    def get_markers_as_geojson(self):
        import json

        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [m["lon"], m["lat"]]},
                "properties": {
                    "name": m["name"],
                    "kind": m["kind"],
                    "country": m["country"],
                    "priority": m["priority"],
                    "pop": m["pop"],
                    "capital": m["capital"],
                    "color": m["color"],
                    "timezone": m["timezone"],
                    "t": m["wx_temp_c"],
                    "rh": m["wx_humidity_pct"],
                    "ws": m["wx_wind_ms"],
                    "wd": m["wx_wind_dir_deg"],
                    "wx_valid_time": m["wx_valid_time"],
                },
            }
            for m in self._markers.values()
        ]
        return json.dumps({"type": "FeatureCollection", "features": features})
