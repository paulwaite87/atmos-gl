import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from worldmap.db.engine import Session
from worldmap.db.models import Storm, StormTrack

logger = logging.getLogger(__name__)


def _cone_wkt(cone_vertices):
    """Converts cone vertices (list of (lon, lat) tuples) into a PostGIS Polygon WKT
    string, closing the ring if needed. Returns None if there aren't enough vertices."""
    if not cone_vertices or len(cone_vertices) < 3:
        return None
    if cone_vertices[0] != cone_vertices[-1]:
        cone_vertices = [*cone_vertices, cone_vertices[0]]
    coords = ",".join(f"{lon} {lat}" for lon, lat in cone_vertices)
    return f"POLYGON(({coords}))"


class StormAdapter:
    """Real adapter for storms + storm_track, backed by SQLAlchemy."""

    def update_storm(self, sid, name, cone_vertices, track_points):
        """Updates the master storm record and completely refreshes its track
        history/forecast. cone_vertices: list of (lon, lat) tuples defining the error
        cone. track_points: list of dicts with keys: LAT, LON, TIME, TYPE, TAU."""
        cone_wkt = _cone_wkt(cone_vertices)
        stmt = pg_insert(Storm).values(
            sid=sid,
            name=name,
            cone_geom=func.ST_GeomFromText(cone_wkt, 4326) if cone_wkt else None,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Storm.sid],
            set_={
                "name": stmt.excluded.name,
                "cone_geom": stmt.excluded.cone_geom,
                "updated_at": func.now(),
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.execute(delete(StormTrack).where(StormTrack.sid == sid))
                if track_points:
                    rows = [
                        {
                            "sid": sid,
                            "record_type": pt["TYPE"],
                            "dt": pt.get("TIME"),
                            "tau": pt.get("TAU", 0),
                            "lat": pt["LAT"],
                            "lon": pt["LON"],
                            "geom": f"SRID=4326;POINT({pt['LON']} {pt['LAT']})",
                        }
                        for pt in track_points
                    ]
                    session.execute(insert(StormTrack), rows)
                session.commit()
        except Exception as e:
            logger.error(f"Error updating storm {sid} in database: {e}", exc_info=True)

    def update_storm_cone(self, sid, cone_vertices):
        """Updates only the cone geometry for a specific storm."""
        stmt = (
            Storm.__table__.update()
            .where(Storm.sid == sid)
            .values(cone_geom=func.ST_GeomFromGeoJSON(json.dumps(cone_vertices)))
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
                logger.info(f"Retrospectively updated cone for storm {sid}")
        except Exception as e:
            logger.error(f"Error updating cone for {sid}: {e}")

    def get_storms_as_geojson(self):
        """Compiles active storms, tracks, and cones into a single GeoJSON
        FeatureCollection."""
        cone_features = select(
            func.jsonb_build_object(
                "type",
                "Feature",
                "geometry",
                cast(func.ST_AsGeoJSON(Storm.cone_geom), JSONB),
                "properties",
                func.jsonb_build_object("feature_type", "CONE", "sid", Storm.sid, "name", Storm.name),
            ).label("feature")
        ).where(Storm.cone_geom.isnot(None))

        def _track_line(record_types, feature_type):
            line = func.ST_MakeLine(aggregate_order_by(StormTrack.geom, StormTrack.dt))
            return (
                select(
                    func.jsonb_build_object(
                        "type",
                        "Feature",
                        "geometry",
                        cast(func.ST_AsGeoJSON(line), JSONB),
                        "properties",
                        func.jsonb_build_object("feature_type", feature_type, "sid", StormTrack.sid),
                    ).label("feature")
                )
                .where(StormTrack.record_type.in_(record_types))
                .group_by(StormTrack.sid)
                .having(func.count(StormTrack.geom) > 1)
            )

        track_past = _track_line(["PAST", "CURRENT"], "TRACK_PAST")
        track_forecast = _track_line(["CURRENT", "FORECAST"], "TRACK_FORECAST")

        point_features = (
            select(
                func.jsonb_build_object(
                    "type",
                    "Feature",
                    "geometry",
                    cast(func.ST_AsGeoJSON(StormTrack.geom), JSONB),
                    "properties",
                    func.jsonb_build_object(
                        "feature_type",
                        "POINT",
                        "sid",
                        StormTrack.sid,
                        "name",
                        Storm.name,
                        "record_type",
                        StormTrack.record_type,
                        "tau",
                        StormTrack.tau,
                        "dt",
                        StormTrack.dt,
                    ),
                ).label("feature")
            )
            .select_from(StormTrack)
            .join(Storm, StormTrack.sid == Storm.sid)
        )

        subquery = cone_features.union_all(track_past, track_forecast, point_features).subquery()
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(func.jsonb_agg(subquery.c.feature), text("'[]'::jsonb")),
        )
        stmt = select(cast(collection, SqlText)).select_from(subquery)
        try:
            with Session() as session:
                result = session.scalar(stmt)
                if result is not None:
                    return result
        except Exception as e:
            logger.error(f"Error fetching storms geojson: {e}")
        return '{"type":"FeatureCollection","features":[]}'

    def prune_expired_storms(self, expiry_days=4):
        """Removes storms that haven't been updated recently (storm_track rows cascade
        via the FK's ON DELETE CASCADE)."""
        cutoff = func.now() - timedelta(days=expiry_days)
        try:
            with Session() as session:
                result = session.execute(delete(Storm).where(Storm.updated_at < cutoff))
                session.commit()
                if result.rowcount > 0:
                    logger.info(f"Pruned {result.rowcount} expired storms from database.")
        except Exception as e:
            logger.error(f"Error pruning expired storms: {e}")


class FakeStormAdapter:
    """In-memory fake for storms + storm_track, matching StormAdapter's method
    contracts (including the FK ON DELETE CASCADE between them)."""

    def __init__(self):
        self._storms: dict[str, dict] = {}
        self._tracks: dict[str, list[dict]] = {}

    def update_storm(self, sid, name, cone_vertices, track_points):
        cone_wkt = _cone_wkt(cone_vertices)
        cone_geom = None
        if cone_wkt:
            ring = [*cone_vertices]
            if ring[0] != ring[-1]:
                ring = [*ring, ring[0]]
            cone_geom = {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in ring]]}
        existing = self._storms.get(sid, {})
        existing.update(
            {
                "sid": sid,
                "name": name,
                "cone_geom": cone_geom,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._storms[sid] = existing
        self._tracks[sid] = [
            {
                "sid": sid,
                "record_type": pt["TYPE"],
                "dt": pt.get("TIME"),
                "tau": pt.get("TAU", 0),
                "lat": pt["LAT"],
                "lon": pt["LON"],
            }
            for pt in track_points
        ]

    def update_storm_cone(self, sid, cone_vertices):
        if sid in self._storms:
            self._storms[sid]["cone_geom"] = cone_vertices

    def get_storms_as_geojson(self):
        features = []

        for storm in self._storms.values():
            if storm["cone_geom"] is not None:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": storm["cone_geom"],
                        "properties": {
                            "feature_type": "CONE",
                            "sid": storm["sid"],
                            "name": storm["name"],
                        },
                    }
                )

        for sid, points in self._tracks.items():
            for record_types, feature_type in (
                (("PAST", "CURRENT"), "TRACK_PAST"),
                (("CURRENT", "FORECAST"), "TRACK_FORECAST"),
            ):
                matching = sorted(
                    (p for p in points if p["record_type"] in record_types),
                    key=lambda p: p["dt"] or datetime.min.replace(tzinfo=timezone.utc),
                )
                if len(matching) > 1:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[p["lon"], p["lat"]] for p in matching],
                            },
                            "properties": {"feature_type": feature_type, "sid": sid},
                        }
                    )

            storm_name = self._storms.get(sid, {}).get("name")
            for p in points:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": {
                            "feature_type": "POINT",
                            "sid": sid,
                            "name": storm_name,
                            "record_type": p["record_type"],
                            "tau": p["tau"],
                            "dt": p["dt"].isoformat() if p["dt"] else None,
                        },
                    }
                )

        return json.dumps({"type": "FeatureCollection", "features": features})

    def prune_expired_storms(self, expiry_days=4):
        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        expired = [sid for sid, s in self._storms.items() if s["updated_at"] < cutoff]
        for sid in expired:
            del self._storms[sid]
            self._tracks.pop(sid, None)  # FK ON DELETE CASCADE
