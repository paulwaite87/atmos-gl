import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.models import LightningStrike

logger = logging.getLogger(__name__)


class LightningAdapter:
    """Real adapter for lightning_strikes, backed by SQLAlchemy."""

    def update_lightning_strike(self, strike_id, lat, lon, quality, timestamp_iso):
        """UPSERTs a lightning strike into the database with spatial geometry."""
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = pg_insert(LightningStrike).values(
            id=strike_id,
            lat=lat,
            lon=lon,
            geom=point,
            quality=quality,
            acquired_at=timestamp_iso,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[LightningStrike.id])
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error saving lightning strike {strike_id}: {e}")

    def get_lightning_in_region(self, lon_min, lat_min, lon_max, lat_max, expiry_minutes=60):
        """Retrieves strikes within a specific bbox and time window."""
        envelope = func.ST_MakeEnvelope(lon_min, lat_min, lon_max, lat_max, 4326)
        cutoff = func.now() - timedelta(minutes=expiry_minutes)
        stmt = select(
            LightningStrike.lat, LightningStrike.lon, LightningStrike.acquired_at.label("timestamp")
        ).where(
            LightningStrike.geom.op("&&")(envelope),
            LightningStrike.acquired_at > cutoff,
        )
        try:
            with Session() as session:
                rows = session.execute(stmt).all()
                return [{"lat": r.lat, "lon": r.lon, "timestamp": r.timestamp} for r in rows]
        except Exception as e:
            logger.error(f"Error fetching lightning for region: {e}")
            return []

    def get_lightning_as_geojson(self, expiry_hours=2):
        """Returns lightning strikes within the expiry window as a GeoJSON string."""
        age_minutes = func.extract(
            "epoch", func.now() - LightningStrike.acquired_at
        ) / 60.0
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(LightningStrike.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "id",
                LightningStrike.id,
                "quality",
                LightningStrike.quality,
                "age_minutes",
                age_minutes,
                "timestamp",
                func.to_char(LightningStrike.acquired_at, "HH24:MI"),
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(func.jsonb_agg(feature), text("'[]'::jsonb")),
        )
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = select(cast(collection, SqlText)).where(LightningStrike.acquired_at >= cutoff)
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building lightning GeoJSON: {e}")
            return '{"type":"FeatureCollection","features":[]}'

    def prune_lightning(self, expiry_hours=24):
        """Deletes old lightning data to keep the table performant."""
        cutoff = func.now() - timedelta(hours=expiry_hours)
        try:
            with Session() as session:
                result = session.execute(
                    delete(LightningStrike).where(LightningStrike.acquired_at < cutoff)
                )
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error pruning lightning: {e}")
            return 0


class FakeLightningAdapter:
    """In-memory fake for lightning_strikes, matching LightningAdapter's method contracts."""

    def __init__(self):
        self._strikes: dict[str, dict] = {}

    def update_lightning_strike(self, strike_id, lat, lon, quality, timestamp_iso):
        if strike_id in self._strikes:
            return
        acquired_at = datetime.fromisoformat(timestamp_iso)
        self._strikes[strike_id] = {
            "id": strike_id,
            "lat": lat,
            "lon": lon,
            "quality": quality,
            "acquired_at": acquired_at,
        }

    def get_lightning_in_region(self, lon_min, lat_min, lon_max, lat_max, expiry_minutes=60):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=expiry_minutes)
        hits = []
        for s in self._strikes.values():
            if not (lon_min <= s["lon"] <= lon_max and lat_min <= s["lat"] <= lat_max):
                continue
            if s["acquired_at"] <= cutoff:
                continue
            hits.append({"lat": s["lat"], "lon": s["lon"], "timestamp": s["acquired_at"]})
        return hits

    def get_lightning_as_geojson(self, expiry_hours=2):
        import json

        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        now = datetime.now(timezone.utc)
        features = []
        for s in self._strikes.values():
            if s["acquired_at"] < cutoff:
                continue
            age_minutes = (now - s["acquired_at"]).total_seconds() / 60.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                    "properties": {
                        "id": s["id"],
                        "quality": s["quality"],
                        "age_minutes": age_minutes,
                        "timestamp": s["acquired_at"].strftime("%H:%M"),
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def prune_lightning(self, expiry_hours=24):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        before = len(self._strikes)
        self._strikes = {k: v for k, v in self._strikes.items() if v["acquired_at"] >= cutoff}
        return before - len(self._strikes)
