import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, func, select, delete
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import Fire

logger = logging.getLogger(__name__)

# Postgres caps bind parameters at 65535 per statement; each row here binds 9 (all
# columns except geom, which is a literal EWKT string built per-row, same technique
# marker_adapter.py's upsert_markers uses to keep bulk inserts to plain param dicts
# rather than a ST_MakePoint(...) function call that can't vary per row in one execute).
# 5000 rows/chunk stays well clear of that limit while keeping round-trips to Postgres
# low even at VIIRS' full-world daily volume (tens of thousands of rows).
_UPSERT_CHUNK_SIZE = 5000

# Ordinal ranking of FIRMS' text confidence levels, so "min_confidence=nominal" can be
# expressed as a >= comparison despite Postgres having no native ordinal type here.
CONFIDENCE_RANK = {"low": 0, "nominal": 1, "high": 2}


def _confidence_at_or_above(min_confidence: str) -> list[str]:
    min_rank = CONFIDENCE_RANK.get(min_confidence, 0)
    return [c for c, rank in CONFIDENCE_RANK.items() if rank >= min_rank]


class FireAdapter:
    """Real adapter for NASA FIRMS active-fire detections, backed by SQLAlchemy."""

    def upsert_fires(self, rows):
        """Bulk-UPSERTs fire detections into the database. Each row is a dict with keys:
        id, lat, lon, brightness, frp, confidence, satellite, daynight, acq_time (ISO str).

        Bulk, not one UPSERT per detection: VIIRS' global per-cycle volume (thousands to
        tens of thousands of rows) made a one-Session-per-row loop (this adapter's
        original shape, mirroring quake_adapter.py) far too slow -- a single collect()
        cycle didn't finish within several minutes. Follows marker_adapter.py's
        upsert_markers bulk pattern instead, chunked by _UPSERT_CHUNK_SIZE."""
        if not rows:
            return
        values = [
            {**r, "geom": f"SRID=4326;POINT({r['lon']} {r['lat']})"}
            for r in rows
        ]
        stmt = pg_insert(Fire)
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
                for i in range(0, len(values), _UPSERT_CHUNK_SIZE):
                    session.execute(stmt, values[i : i + _UPSERT_CHUNK_SIZE])
                session.commit()
        except Exception as e:
            logger.error(f"Error bulk-saving {len(rows)} fires: {e}")

    def get_fires_as_geojson(self, min_confidence="low", expiry_hours=24, max_frp=5000.0):
        """Returns fire detections as GeoJSON, filtering by age, confidence tier, and an
        FRP ceiling (max_frp, megawatts).

        The ceiling exists because VIIRS' thermal-anomaly detector doesn't distinguish
        wildfires from gas flares, industrial furnaces, or the rare sensor artifact --
        it flags anything sufficiently hot. Real wildfire fronts, even the most extreme
        recorded (e.g. Australia's 2019-2020 "Black Summer"), top out in the
        low-thousands of MW per pixel; readings far above that are far more likely a
        flare/industrial source than an actual fire. 5000 MW is a deliberately generous
        default -- comfortably above genuine extreme-fire-behaviour readings, but well
        below the 12,000+ MW outliers (fixed industrial coordinates, not moving/growing
        night to night like a real fire front) that prompted this filter."""
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
            Fire.frp <= max_frp,
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

    def upsert_fires(self, rows):
        if not rows:
            return
        for r in rows:
            existing = self._fires.get(r["id"])
            if existing is None:
                self._fires[r["id"]] = {
                    "id": r["id"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "brightness": r["brightness"],
                    "frp": r["frp"],
                    "confidence": r["confidence"],
                    "satellite": r["satellite"],
                    "daynight": r["daynight"],
                    "acq_time": datetime.fromisoformat(r["acq_time"]),
                }
                continue
            # ON CONFLICT only updates brightness/frp/confidence; lat/lon (geom),
            # satellite, daynight, acq_time are immutable, mirroring the real adapter's
            # set_ list.
            existing["brightness"] = r["brightness"]
            existing["frp"] = r["frp"]
            existing["confidence"] = r["confidence"]

    def get_fires_as_geojson(self, min_confidence="low", expiry_hours=24, max_frp=5000.0):
        import json

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=expiry_hours)
        allowed = set(_confidence_at_or_above(min_confidence))
        features = []
        for f in self._fires.values():
            if f["acq_time"] < cutoff or f["confidence"] not in allowed or f["frp"] > max_frp:
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
