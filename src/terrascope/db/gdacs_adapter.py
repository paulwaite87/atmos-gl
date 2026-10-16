import json
import logging
from datetime import datetime, timedelta, timezone

from shapely.geometry import mapping, shape
from sqlalchemy import cast, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    aggregate_order_by,
    insert as pg_insert,
)
from sqlalchemy.types import Text as SqlText

from terrascope.db.engine import Session
from terrascope.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from terrascope.db.models import GdacsEvent, GdacsFootprint

logger = logging.getLogger(__name__)

# Event fields that change as GDACS advances an event's episode -- everything except the
# (event_type, event_id) key. lat/lon/geom move too (a cyclone's centroid follows its track).
_MUTABLE_FIELDS = (
    "episode_id",
    "name",
    "alert_level",
    "alert_score",
    "severity",
    "severity_text",
    "severity_unit",
    "country",
    "iso3",
    "from_date",
    "to_date",
    "date_modified",
    "is_current",
    "report_url",
    "lat",
    "lon",
)
_DATE_FIELDS = ("from_date", "to_date", "date_modified")
# Properties served on the points GeoJSON, in popup order.
_PROPERTY_FIELDS = (
    "event_type",
    "event_id",
    "name",
    "alert_level",
    "alert_score",
    "severity",
    "severity_text",
    "severity_unit",
    "country",
    "iso3",
    "from_date",
    "to_date",
    "is_current",
    "report_url",
)
_UTC_FORMAT_SQL = 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
_UTC_FORMAT_PY = "%Y-%m-%dT%H:%M:%SZ"
# Footprints are simplified before storage: ~0.01 degree (about 1 km) keeps an outline
# recognisable while dropping the vertex detail that makes raw flood areas megabytes big.
FOOTPRINT_SIMPLIFY_DEG = 0.01


def _parse_utc(value):
    """GDACS dates are naive ISO strings in UTC."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class GdacsAdapter:
    """Real adapter for GDACS events and footprints, backed by SQLAlchemy."""

    def upsert_event(self, event: dict) -> None:
        """UPSERTs a GDACS event keyed on (event_type, event_id)."""
        values = {k: event[k] for k in _MUTABLE_FIELDS}
        for k in _DATE_FIELDS:
            values[k] = _parse_utc(values[k])
        point = func.ST_SetSRID(func.ST_MakePoint(event["lon"], event["lat"]), 4326)
        stmt = pg_insert(GdacsEvent).values(
            event_type=event["event_type"],
            event_id=event["event_id"],
            geom=point,
            **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[GdacsEvent.event_type, GdacsEvent.event_id],
            set_={
                **{k: stmt.excluded[k] for k in _MUTABLE_FIELDS},
                "geom": stmt.excluded.geom,
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(
                f"Error saving GDACS event {event['event_type']}/{event['event_id']}: {e}"
            )

    def get_events_as_geojson(self, grace_days: int = 7) -> str:
        """Events as GeoJSON points: every current event, plus ended ones whose end date
        is within `grace_days`."""
        props = []
        for field in _PROPERTY_FIELDS:
            column = getattr(GdacsEvent, field)
            if field in _DATE_FIELDS:
                column = func.to_char(column.op("AT TIME ZONE")("UTC"), _UTC_FORMAT_SQL)
            props += [field, column]
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(GdacsEvent.geom), JSONB),
            "properties",
            func.jsonb_build_object(*props),
        )
        cutoff = func.now() - timedelta(days=grace_days)
        stmt = select(cast(as_feature_collection(feature), SqlText)).where(
            or_(GdacsEvent.is_current.is_(True), GdacsEvent.to_date >= cutoff)
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building GDACS GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def replace_footprint(
        self, event_type: str, event_id: int, source_modified: str, parts: list[dict]
    ) -> None:
        """Replaces all of an event's footprint geometry with `parts` -- a list of
        {"feature_class": str, "geometry": GeoJSON geometry dict} -- simplified."""
        rows = []
        for index, part in enumerate(parts):
            geom = func.ST_SimplifyPreserveTopology(
                func.ST_SetSRID(
                    func.ST_GeomFromGeoJSON(json.dumps(part["geometry"])), 4326
                ),
                FOOTPRINT_SIMPLIFY_DEG,
            )
            rows.append(
                {
                    "event_type": event_type,
                    "event_id": event_id,
                    "part_index": index,
                    "feature_class": part["feature_class"],
                    "source_modified": source_modified,
                    "geom": geom,
                }
            )
        try:
            with Session() as session:
                session.execute(
                    delete(GdacsFootprint).where(
                        GdacsFootprint.event_type == event_type,
                        GdacsFootprint.event_id == event_id,
                    )
                )
                if rows:
                    session.execute(pg_insert(GdacsFootprint).values(rows))
                session.commit()
        except Exception as e:
            logger.error(f"Error saving GDACS footprint {event_type}/{event_id}: {e}")

    def get_footprint_as_geojson(self, event_type: str, event_id: int) -> str:
        """One event's stored footprint as a FeatureCollection, in stored (GDACS) order."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(GdacsFootprint.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "event_type",
                GdacsFootprint.event_type,
                "event_id",
                GdacsFootprint.event_id,
                "feature_class",
                GdacsFootprint.feature_class,
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(
                func.jsonb_agg(
                    aggregate_order_by(feature, GdacsFootprint.feature_class)
                ),
                text("'[]'::jsonb"),
            ),
        )
        stmt = select(cast(collection, SqlText)).where(
            GdacsFootprint.event_type == event_type, GdacsFootprint.event_id == event_id
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building GDACS footprint GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def footprint_marker(self, event_type: str, event_id: int) -> str | None:
        """The GDACS modified-timestamp the stored footprint was fetched at, or None."""
        stmt = (
            select(GdacsFootprint.source_modified)
            .where(
                GdacsFootprint.event_type == event_type,
                GdacsFootprint.event_id == event_id,
            )
            .limit(1)
        )
        try:
            with Session() as session:
                return session.scalar(stmt)
        except Exception as e:
            logger.error(f"Error reading GDACS footprint marker: {e}")
            return None

    def prune_expired(self, expiry_days: float) -> int:
        """Deletes events GDACS no longer flags current whose end date is more than
        `expiry_days` old; their footprints go with them (ON DELETE CASCADE). Returns the
        number of events deleted."""
        cutoff = func.now() - timedelta(days=expiry_days)
        stmt = delete(GdacsEvent).where(
            GdacsEvent.is_current.is_not(True), GdacsEvent.to_date < cutoff
        )
        with Session() as session:
            result = session.execute(stmt)
            session.commit()
            return result.rowcount


class FakeGdacsAdapter:
    """In-memory fake for GDACS events, matching GdacsAdapter's method contracts."""

    def __init__(self):
        self._events: dict[tuple[str, int], dict] = {}
        # (event_type, event_id) -> (source_modified, [(feature_class, GeoJSON geometry)])
        self._footprints: dict[tuple[str, int], tuple[str, list]] = {}

    def upsert_event(self, event: dict) -> None:
        stored = dict(event)
        for k in _DATE_FIELDS:
            stored[k] = _parse_utc(stored[k])
        self._events[(event["event_type"], event["event_id"])] = stored

    def get_events_as_geojson(self, grace_days: int = 7) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
        features = []
        for e in self._events.values():
            if not (e["is_current"] or e["to_date"] >= cutoff):
                continue
            props = {}
            for field in _PROPERTY_FIELDS:
                value = e[field]
                if field in _DATE_FIELDS:
                    value = value.strftime(_UTC_FORMAT_PY)
                props[field] = value
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": props,
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def replace_footprint(
        self, event_type: str, event_id: int, source_modified: str, parts: list[dict]
    ) -> None:
        simplified = []
        for part in parts:
            geom = shape(part["geometry"]).simplify(
                FOOTPRINT_SIMPLIFY_DEG, preserve_topology=True
            )
            simplified.append((part["feature_class"], mapping(geom)))
        self._footprints[(event_type, event_id)] = (source_modified, simplified)

    def get_footprint_as_geojson(self, event_type: str, event_id: int) -> str:
        _, parts = self._footprints.get((event_type, event_id), (None, []))
        features = [
            {
                "type": "Feature",
                "geometry": json.loads(json.dumps(geometry)),
                "properties": {
                    "event_type": event_type,
                    "event_id": event_id,
                    "feature_class": feature_class,
                },
            }
            for feature_class, geometry in parts
        ]
        return json.dumps({"type": "FeatureCollection", "features": features})

    def footprint_marker(self, event_type: str, event_id: int) -> str | None:
        marker, _ = self._footprints.get((event_type, event_id), (None, []))
        return marker

    def prune_expired(self, expiry_days: float) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        expired = [
            key
            for key, e in self._events.items()
            if not e["is_current"] and e["to_date"] < cutoff
        ]
        for key in expired:
            del self._events[key]
            self._footprints.pop(key, None)
        return len(expired)
