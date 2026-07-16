import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, func, select, delete
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import Fire

logger = logging.getLogger(__name__)

# Ordinal ranking of FIRMS' text confidence levels, so "min_confidence=nominal" can be
# expressed as a >= comparison despite Postgres having no native ordinal type here.
CONFIDENCE_RANK = {"low": 0, "nominal": 1, "high": 2}


def _confidence_at_or_above(min_confidence: str) -> list[str]:
    min_rank = CONFIDENCE_RANK.get(min_confidence, 0)
    return [c for c, rank in CONFIDENCE_RANK.items() if rank >= min_rank]


class FireAdapter:
    """Real adapter for NASA FIRMS active-fire detections, backed by SQLAlchemy."""

    def update_fire(self, fire_id, lat, lon, brightness, frp, confidence, satellite, daynight, acq_time_iso):
        """UPSERTs a fire detection into the database."""
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = pg_insert(Fire).values(
            id=fire_id,
            lat=lat,
            lon=lon,
            brightness=brightness,
            frp=frp,
            confidence=confidence,
            satellite=satellite,
            daynight=daynight,
            acq_time=acq_time_iso,
            geom=point,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Fire.id],
            set_={
                "brightness": stmt.excluded.brightness,
                "frp": stmt.excluded.frp,
                "confidence": stmt.excluded.confidence,
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error saving fire {fire_id}: {e}")

    def get_fires_as_geojson(self, min_confidence="low", expiry_hours=24):
        """Returns fire detections as GeoJSON, filtering by age and confidence tier."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Fire.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "id",
                Fire.id,
                "brightness",
                Fire.brightness,
                "frp",
                Fire.frp,
                "confidence",
                Fire.confidence,
                "satellite",
                Fire.satellite,
                "daynight",
                Fire.daynight,
                "age_minutes",
                func.extract("epoch", func.now() - Fire.acq_time) / 60.0,
            ),
        )
        collection = as_feature_collection(feature)
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = select(cast(collection, SqlText)).where(
            Fire.acq_time >= cutoff,
            Fire.confidence.in_(_confidence_at_or_above(min_confidence)),
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building fire GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def delete_expired(self, expiry_hours) -> int:
        """Deletes fire rows older than expiry_hours, keeping the table bounded --
        VIIRS' global detection volume (thousands/day) is orders of magnitude higher
        than quakes/volcanoes, which never prune. Returns the number of rows deleted."""
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = delete(Fire).where(Fire.acq_time < cutoff)
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error deleting expired fires: {e}")
            return 0


class FakeFireAdapter:
    """In-memory fake for fire detections, matching FireAdapter's method contracts."""

    def __init__(self):
        self._fires: dict[str, dict] = {}

    def update_fire(self, fire_id, lat, lon, brightness, frp, confidence, satellite, daynight, acq_time_iso):
        existing = self._fires.get(fire_id)
        if existing is None:
            self._fires[fire_id] = {
                "id": fire_id,
                "lat": lat,
                "lon": lon,
                "brightness": brightness,
                "frp": frp,
                "confidence": confidence,
                "satellite": satellite,
                "daynight": daynight,
                "acq_time": datetime.fromisoformat(acq_time_iso),
            }
            return
        # ON CONFLICT only updates brightness/frp/confidence; lat/lon (geom), satellite,
        # daynight, acq_time are immutable, mirroring the real adapter's set_ list.
        existing["brightness"] = brightness
        existing["frp"] = frp
        existing["confidence"] = confidence

    def get_fires_as_geojson(self, min_confidence="low", expiry_hours=24):
        import json

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=expiry_hours)
        allowed = set(_confidence_at_or_above(min_confidence))
        features = []
        for f in self._fires.values():
            if f["acq_time"] < cutoff or f["confidence"] not in allowed:
                continue
            age_minutes = (now - f["acq_time"]).total_seconds() / 60.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
                    "properties": {
                        "id": f["id"],
                        "brightness": f["brightness"],
                        "frp": f["frp"],
                        "confidence": f["confidence"],
                        "satellite": f["satellite"],
                        "daynight": f["daynight"],
                        "age_minutes": age_minutes,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def delete_expired(self, expiry_hours) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=expiry_hours)
        expired = [fid for fid, f in self._fires.items() if f["acq_time"] < cutoff]
        for fid in expired:
            del self._fires[fid]
        return len(expired)
