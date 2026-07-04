import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from worldmap.db.engine import Session
from worldmap.db.models import Earthquake

logger = logging.getLogger(__name__)


class QuakeAdapter:
    """Real adapter for earthquakes, backed by SQLAlchemy."""

    def update_quake(self, quake_id, mag, depth, place, time_iso, lat, lon):
        """UPSERTs an earthquake into the database."""
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = pg_insert(Earthquake).values(
            id=quake_id,
            mag=mag,
            depth=depth,
            place=place,
            eq_time=time_iso,
            lat=lat,
            lon=lon,
            geom=point,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Earthquake.id],
            set_={
                "mag": stmt.excluded.mag,
                "depth": stmt.excluded.depth,
                "place": stmt.excluded.place,
                "eq_time": stmt.excluded.eq_time,
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error saving earthquake {quake_id}: {e}")

    def get_quakes_as_geojson(self, min_mag=3.5, expiry_hours=12, recent_hours=3):
        """Returns earthquakes as GeoJSON, filtering by age and magnitude."""
        age_hours = func.extract("epoch", func.now() - Earthquake.eq_time) / 3600.0
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Earthquake.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "id",
                Earthquake.id,
                "mag",
                Earthquake.mag,
                "depth",
                Earthquake.depth,
                "place",
                Earthquake.place,
                "age_minutes",
                func.extract("epoch", func.now() - Earthquake.eq_time) / 60.0,
                "is_recent",
                age_hours <= recent_hours,
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(func.jsonb_agg(feature), text("'[]'::jsonb")),
        )
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = select(cast(collection, SqlText)).where(
            Earthquake.eq_time >= cutoff, Earthquake.mag >= min_mag
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building quake GeoJSON: {e}")
            return '{"type":"FeatureCollection","features":[]}'


class FakeQuakeAdapter:
    """In-memory fake for earthquakes, matching QuakeAdapter's method contracts."""

    def __init__(self):
        self._quakes: dict[str, dict] = {}

    def update_quake(self, quake_id, mag, depth, place, time_iso, lat, lon):
        existing = self._quakes.get(quake_id)
        if existing is None:
            self._quakes[quake_id] = {
                "id": quake_id,
                "mag": mag,
                "depth": depth,
                "place": place,
                "eq_time": datetime.fromisoformat(time_iso),
                "lat": lat,
                "lon": lon,
            }
            return
        # ON CONFLICT only updates mag/depth/place/eq_time; lat/lon (geom) is immutable.
        existing["mag"] = mag
        existing["depth"] = depth
        existing["place"] = place
        existing["eq_time"] = datetime.fromisoformat(time_iso)

    def get_quakes_as_geojson(self, min_mag=3.5, expiry_hours=12, recent_hours=3):
        import json

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=expiry_hours)
        features = []
        for q in self._quakes.values():
            if q["eq_time"] < cutoff or q["mag"] < min_mag:
                continue
            age_minutes = (now - q["eq_time"]).total_seconds() / 60.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [q["lon"], q["lat"]]},
                    "properties": {
                        "id": q["id"],
                        "mag": q["mag"],
                        "depth": q["depth"],
                        "place": q["place"],
                        "age_minutes": age_minutes,
                        "is_recent": (age_minutes / 60.0) <= recent_hours,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})
