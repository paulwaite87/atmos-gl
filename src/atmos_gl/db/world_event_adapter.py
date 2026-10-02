import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, func, select, delete, or_
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import WorldEvent, WorldEventExportFile

logger = logging.getLogger(__name__)

# Categories max_conflict_tone applies to. Diplomacy is exempt: its coverage tone is
# naturally either sign, whereas a "conflict" event whose coverage reads positively is
# almost always GDELT coding figurative "battle"/"fight" language (sitcom round-ups,
# business deals, awards) -- confirmed by sampling live rows, see get_events_as_geojson.
_TONE_FILTERED_CATEGORIES = ("explosion", "warfare", "targeted_violence")

# Same chunking rationale as FireAdapter/MarkerAdapter's bulk upserts: a backfill run
# can cover several days of curated-category GDELT events in one collect() cycle, well
# past a single-statement-per-row shape's comfortable size.
_UPSERT_CHUNK_SIZE = 5000


class WorldEventAdapter:
    """Real adapter for world_events (GDELT-sourced conflict/diplomacy point markers,
    see collectors/world_events.py), backed by SQLAlchemy."""

    def upsert_events(self, rows):
        """Bulk-UPSERTs classified GDELT events. Each row is a dict with keys: id
        (GLOBALEVENTID), category, event_code, actor1_name, actor2_name,
        action_geo_full_name, lat, lon, event_date (ISO str), num_mentions,
        num_sources, goldstein_scale, avg_tone, source_url.

        A re-ingested id (a backfill re-covering a window collect() already filled)
        just refreshes the same row -- GDELT's own fields for a given event don't
        change after the fact, so the ON CONFLICT SET list is the full column set.

        Returns True on success (including nothing to write), False if the write
        failed -- WorldEventsCollector only marks an export file processed on True, so
        a failed write is retried next cycle rather than silently leaving a gap."""
        if not rows:
            return True
        values = [
            {**r, "geom": f"SRID=4326;POINT({r['lon']} {r['lat']})"}
            for r in rows
            if r.get("lat") is not None and r.get("lon") is not None
        ]
        if not values:
            return True
        stmt = pg_insert(WorldEvent)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WorldEvent.id],
            set_={
                "category": stmt.excluded.category,
                "event_code": stmt.excluded.event_code,
                "actor1_name": stmt.excluded.actor1_name,
                "actor2_name": stmt.excluded.actor2_name,
                "action_geo_full_name": stmt.excluded.action_geo_full_name,
                "lat": stmt.excluded.lat,
                "lon": stmt.excluded.lon,
                "geom": stmt.excluded.geom,
                "event_date": stmt.excluded.event_date,
                "num_mentions": stmt.excluded.num_mentions,
                "num_sources": stmt.excluded.num_sources,
                "goldstein_scale": stmt.excluded.goldstein_scale,
                "avg_tone": stmt.excluded.avg_tone,
                "source_url": stmt.excluded.source_url,
            },
        )
        try:
            with Session() as session:
                for i in range(0, len(values), _UPSERT_CHUNK_SIZE):
                    session.execute(stmt, values[i : i + _UPSERT_CHUNK_SIZE])
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Error bulk-saving {len(values)} world events: {e}")
            return False

    def processed_export_slots(self, since):
        """Slot timestamps of every GDELT export file already processed (status ok or
        missing) at or after `since` -- WorldEventsCollector diffs the backfill
        window's expected slots against this to find gaps. Raises on a DB error (an
        empty set would wrongly mean "nothing covered" and trigger a full re-fetch)."""
        with Session() as session:
            return set(
                session.scalars(
                    select(WorldEventExportFile.slot).where(WorldEventExportFile.slot >= since)
                )
            )

    def mark_export_processed(self, slot, status, row_count=0):
        """Records one export file as processed ("ok" or "missing"); idempotent."""
        stmt = pg_insert(WorldEventExportFile).values(
            slot=slot, status=status, row_count=row_count
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WorldEventExportFile.slot],
            set_={
                "status": stmt.excluded.status,
                "row_count": stmt.excluded.row_count,
                "processed_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def prune_export_slots(self, before) -> int:
        """Deletes processed-file records older than `before` (the backfill window's
        start) -- they no longer affect coverage. Returns the number deleted."""
        stmt = delete(WorldEventExportFile).where(WorldEventExportFile.slot < before)
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error pruning world event export file records: {e}")
            return 0

    def get_events_as_geojson(self, expiry_days=7, max_conflict_tone=None):
        """Returns world events as GeoJSON, filtering by age. age_hours (like quakes'
        age_minutes) lets the frontend de-emphasize older events within the window.

        max_conflict_tone (None = off) is a read-time quality filter: a conflict-
        category event (_TONE_FILTERED_CATEGORIES) whose GDELT avg_tone is ABOVE it is
        dropped. An event with no avg_tone is kept -- there's nothing to judge it by.
        Read-time rather than at collection, like expiry_days, so every row is still
        stored and the threshold can be retuned against existing history."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(WorldEvent.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "id",
                WorldEvent.id,
                "category",
                WorldEvent.category,
                "event_code",
                WorldEvent.event_code,
                "actor1_name",
                WorldEvent.actor1_name,
                "actor2_name",
                WorldEvent.actor2_name,
                "place",
                WorldEvent.action_geo_full_name,
                "event_date",
                WorldEvent.event_date,
                "num_mentions",
                WorldEvent.num_mentions,
                "num_sources",
                WorldEvent.num_sources,
                "source_url",
                WorldEvent.source_url,
                "age_hours",
                func.extract("epoch", func.now() - WorldEvent.event_date) / 3600.0,
            ),
        )
        collection = as_feature_collection(feature)
        cutoff = func.now() - timedelta(days=expiry_days)
        stmt = select(cast(collection, SqlText)).where(WorldEvent.event_date >= cutoff)
        if max_conflict_tone is not None:
            stmt = stmt.where(
                or_(
                    WorldEvent.category.notin_(_TONE_FILTERED_CATEGORIES),
                    WorldEvent.avg_tone.is_(None),
                    WorldEvent.avg_tone <= max_conflict_tone,
                )
            )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building world events GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def delete_expired(self, expiry_days=7) -> int:
        """Deletes world_event rows older than expiry_days. Returns the number deleted."""
        cutoff = func.now() - timedelta(days=expiry_days)
        stmt = delete(WorldEvent).where(WorldEvent.event_date < cutoff)
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error deleting expired world events: {e}")
            return 0


class FakeWorldEventAdapter:
    """In-memory fake for world_events, matching WorldEventAdapter's method contracts."""

    def __init__(self):
        self._events: dict[str, dict] = {}
        self._export_files: dict[datetime, dict] = {}

    def upsert_events(self, rows):
        if not rows:
            return True
        for r in rows:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            self._events[r["id"]] = {
                "id": r["id"],
                "category": r["category"],
                "event_code": r.get("event_code"),
                "actor1_name": r.get("actor1_name"),
                "actor2_name": r.get("actor2_name"),
                "action_geo_full_name": r.get("action_geo_full_name"),
                "lat": r["lat"],
                "lon": r["lon"],
                "event_date": datetime.fromisoformat(r["event_date"]),
                "num_mentions": r.get("num_mentions"),
                "num_sources": r.get("num_sources"),
                "goldstein_scale": r.get("goldstein_scale"),
                "avg_tone": r.get("avg_tone"),
                "source_url": r.get("source_url"),
            }
        return True

    def processed_export_slots(self, since):
        return {slot for slot in self._export_files if slot >= since}

    def mark_export_processed(self, slot, status, row_count=0):
        self._export_files[slot] = {"status": status, "row_count": row_count}

    def prune_export_slots(self, before) -> int:
        stale = [slot for slot in self._export_files if slot < before]
        for slot in stale:
            del self._export_files[slot]
        return len(stale)

    def get_events_as_geojson(self, expiry_days=7, max_conflict_tone=None):
        import json

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days)
        features = []
        for e in self._events.values():
            if e["event_date"] < cutoff:
                continue
            if (
                max_conflict_tone is not None
                and e["category"] in _TONE_FILTERED_CATEGORIES
                and e["avg_tone"] is not None
                and e["avg_tone"] > max_conflict_tone
            ):
                continue
            age_hours = (now - e["event_date"]).total_seconds() / 3600.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": {
                        "id": e["id"],
                        "category": e["category"],
                        "event_code": e["event_code"],
                        "actor1_name": e["actor1_name"],
                        "actor2_name": e["actor2_name"],
                        "place": e["action_geo_full_name"],
                        "event_date": e["event_date"].isoformat(),
                        "num_mentions": e["num_mentions"],
                        "num_sources": e["num_sources"],
                        "source_url": e["source_url"],
                        "age_hours": age_hours,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def delete_expired(self, expiry_days=7) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days)
        expired = [eid for eid, e in self._events.items() if e["event_date"] < cutoff]
        for eid in expired:
            del self._events[eid]
        return len(expired)
