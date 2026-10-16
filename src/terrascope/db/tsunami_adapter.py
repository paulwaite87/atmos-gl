import json
import logging

from shapely.geometry import mapping, shape
from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    aggregate_order_by,
    insert as pg_insert,
)
from sqlalchemy.types import Text as SqlText
from datetime import datetime, timedelta, timezone

from terrascope.db.engine import Session
from terrascope.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from terrascope.db.models import TsunamiEvent, TsunamiEventZone, TsunamiZone

logger = logging.getLogger(__name__)

# Zones are simplified before storage: ~0.01 degree (about 1 km) keeps a zone outline
# recognisable while dropping the vertex detail that makes raw NWS zones ~350 KB each.
ZONE_SIMPLIFY_DEG = 0.01
_UTC_FORMAT_PY = "%Y-%m-%dT%H:%M:%SZ"
_DATE_FIELDS = ("origin_time", "expires")
# Properties served on the points GeoJSON, in popup order.
_PROPERTY_FIELDS = (
    "centre",
    "event_id",
    "category",
    "bulletin_number",
    "location_name",
    "magnitude",
    "magnitude_type",
    "depth_km",
    "origin_time",
    "expires",
    "bulletin_url",
    "note",
)


_MUTABLE_FIELDS = tuple(
    f for f in _PROPERTY_FIELDS if f not in ("centre", "event_id")
) + (
    "lat",
    "lon",
)
_UTC_FORMAT_SQL = 'YYYY-MM-DD"T"HH24:MI:SS"Z"'


class TsunamiAdapter:
    """Real adapter for tsunami events and zones, backed by SQLAlchemy."""

    def upsert_event(self, event: dict) -> None:
        """UPSERTs an event keyed on (centre, event_id) and replaces its zone list with
        event["zones"] ({zone_code: category})."""
        values = {k: event[k] for k in _MUTABLE_FIELDS}
        point = func.ST_SetSRID(func.ST_MakePoint(event["lon"], event["lat"]), 4326)
        stmt = pg_insert(TsunamiEvent).values(
            centre=event["centre"], event_id=event["event_id"], geom=point, **values
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TsunamiEvent.centre, TsunamiEvent.event_id],
            set_={
                **{k: stmt.excluded[k] for k in _MUTABLE_FIELDS},
                "geom": stmt.excluded.geom,
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.execute(
                    delete(TsunamiEventZone).where(
                        TsunamiEventZone.centre == event["centre"],
                        TsunamiEventZone.event_id == event["event_id"],
                    )
                )
                if event["zones"]:
                    session.execute(
                        pg_insert(TsunamiEventZone).values(
                            [
                                {
                                    "centre": event["centre"],
                                    "event_id": event["event_id"],
                                    "zone_code": code,
                                    "category": category,
                                }
                                for code, category in event["zones"].items()
                            ]
                        )
                    )
                session.commit()
        except Exception as e:
            logger.error(
                f"Error saving tsunami event {event['centre']}/{event['event_id']}: {e}"
            )

    def get_events_as_geojson(self, grace_days: float = 1) -> str:
        """Events as GeoJSON points: every event whose bulletin has not expired, plus ones
        that expired within `grace_days`."""
        props = []
        for field in _PROPERTY_FIELDS:
            column = getattr(TsunamiEvent, field)
            if field in _DATE_FIELDS:
                column = func.to_char(column.op("AT TIME ZONE")("UTC"), _UTC_FORMAT_SQL)
            props += [field, column]
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(TsunamiEvent.geom), JSONB),
            "properties",
            func.jsonb_build_object(*props),
        )
        cutoff = func.now() - timedelta(days=grace_days)
        stmt = select(cast(as_feature_collection(feature), SqlText)).where(
            TsunamiEvent.expires >= cutoff
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building tsunami GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def known_zone_codes(self) -> set[str]:
        """Codes of the zones whose geometry is already cached."""
        try:
            with Session() as session:
                return set(session.scalars(select(TsunamiZone.zone_code)))
        except Exception as e:
            logger.error(f"Error reading cached tsunami zones: {e}")
            return set()

    def save_zone(self, code: str, geometry: dict) -> None:
        """Caches a zone's geometry (a GeoJSON geometry dict), simplified."""
        geom = func.ST_SimplifyPreserveTopology(
            func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(geometry)), 4326),
            ZONE_SIMPLIFY_DEG,
        )
        stmt = pg_insert(TsunamiZone).values(zone_code=code, geom=geom)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TsunamiZone.zone_code], set_={"geom": stmt.excluded.geom}
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error saving tsunami zone {code}: {e}")

    def get_footprint_as_geojson(self, centre: str, event_id: str) -> str:
        """One event's zones that have cached geometry, as a FeatureCollection."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(TsunamiZone.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "centre",
                TsunamiEventZone.centre,
                "event_id",
                TsunamiEventZone.event_id,
                "zone_code",
                TsunamiEventZone.zone_code,
                "category",
                TsunamiEventZone.category,
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(
                func.jsonb_agg(aggregate_order_by(feature, TsunamiEventZone.zone_code)),
                text("'[]'::jsonb"),
            ),
        )
        stmt = (
            select(cast(collection, SqlText))
            .select_from(TsunamiEventZone)
            .join(TsunamiZone, TsunamiZone.zone_code == TsunamiEventZone.zone_code)
            .where(
                TsunamiEventZone.centre == centre,
                TsunamiEventZone.event_id == event_id,
            )
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building tsunami footprint GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def prune_expired(self, expiry_days: float) -> int:
        """Deletes events whose bulletin expired more than `expiry_days` ago; their zone
        lists go with them (ON DELETE CASCADE) but cached zone geometry stays. Returns the
        number of events deleted."""
        cutoff = func.now() - timedelta(days=expiry_days)
        with Session() as session:
            result = session.execute(
                delete(TsunamiEvent).where(TsunamiEvent.expires < cutoff)
            )
            session.commit()
            return result.rowcount


class FakeTsunamiAdapter:
    """In-memory fake for tsunami events, matching TsunamiAdapter's method contracts."""

    def __init__(self):
        self._events: dict[tuple[str, str], dict] = {}
        self._zones: dict[str, dict] = {}

    def upsert_event(self, event: dict) -> None:
        self._events[(event["centre"], event["event_id"])] = dict(event)

    def known_zone_codes(self) -> set[str]:
        return set(self._zones)

    def save_zone(self, code: str, geometry: dict) -> None:
        simplified = shape(geometry).simplify(ZONE_SIMPLIFY_DEG, preserve_topology=True)
        self._zones[code] = mapping(simplified)

    def get_footprint_as_geojson(self, centre: str, event_id: str) -> str:
        event = self._events.get((centre, event_id), {"zones": {}})
        features = [
            {
                "type": "Feature",
                "geometry": json.loads(json.dumps(self._zones[code])),
                "properties": {
                    "centre": centre,
                    "event_id": event_id,
                    "zone_code": code,
                    "category": category,
                },
            }
            for code, category in sorted(event["zones"].items())
            if code in self._zones
        ]
        return json.dumps({"type": "FeatureCollection", "features": features})

    def get_events_as_geojson(self, grace_days: float = 1) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
        features = []
        for e in self._events.values():
            if e["expires"] < cutoff:
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

    def prune_expired(self, expiry_days: float) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        expired = [k for k, e in self._events.items() if e["expires"] < cutoff]
        for key in expired:
            del self._events[key]
        return len(expired)
