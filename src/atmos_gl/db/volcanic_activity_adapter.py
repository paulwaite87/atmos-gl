import json
import logging

from sqlalchemy import cast, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import VolcanicActivity

logger = logging.getLogger(__name__)


def _is_new(activity_type) -> bool:
    """GVP's own New/Continuing classification, collapsed to a boolean by prefix match
    rather than an exhaustive list -- "New Eruptive Activity"/"New Unrest" -> True,
    "Continuing Eruptive Activity"/(any future "Continuing ..." variant) -> False. A
    GVP activity_type not seen live this session but sharing the same "New "/
    "Continuing " prefix convention still classifies correctly without a code change."""
    return bool(activity_type) and activity_type.strip().lower().startswith("new")


class VolcanicActivityAdapter:
    """Real adapter for volcanic_activity, backed by SQLAlchemy (issue #253)."""

    def upsert_activity(
        self,
        vnum,
        name,
        country,
        lat,
        lon,
        activity_type,
        report_description,
        hans_color_code,
        hans_alert_level,
        hans_notice_url,
    ):
        """Upserts one volcano's current state. lat/lon/name/country are None when
        this call carries only a HANS enrichment for a volcano absent from this week's
        GVP report -- the ON CONFLICT SET list below coalesces against the existing row
        in that case, so a previously-recorded GVP sighting's location/name survives.
        last_seen_at is always bumped: presence in either source this poll is enough to
        count as "seen" (see issue #253's HANS/GVP liveness discussion)."""
        point = (
            func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
            if lat is not None and lon is not None
            else None
        )
        stmt = pg_insert(VolcanicActivity).values(
            vnum=vnum,
            name=name,
            country=country,
            lat=lat,
            lon=lon,
            geom=point,
            activity_type=activity_type,
            report_description=report_description,
            hans_color_code=hans_color_code,
            hans_alert_level=hans_alert_level,
            hans_notice_url=hans_notice_url,
            last_seen_at=func.now(),
        )
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[VolcanicActivity.vnum],
            set_={
                "name": func.coalesce(excluded.name, VolcanicActivity.name),
                "country": func.coalesce(excluded.country, VolcanicActivity.country),
                "lat": func.coalesce(excluded.lat, VolcanicActivity.lat),
                "lon": func.coalesce(excluded.lon, VolcanicActivity.lon),
                "geom": func.coalesce(excluded.geom, VolcanicActivity.geom),
                "activity_type": func.coalesce(excluded.activity_type, VolcanicActivity.activity_type),
                "report_description": func.coalesce(
                    excluded.report_description, VolcanicActivity.report_description
                ),
                "hans_color_code": func.coalesce(excluded.hans_color_code, VolcanicActivity.hans_color_code),
                "hans_alert_level": func.coalesce(excluded.hans_alert_level, VolcanicActivity.hans_alert_level),
                "hans_notice_url": func.coalesce(excluded.hans_notice_url, VolcanicActivity.hans_notice_url),
                "last_seen_at": func.now(),
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error upserting volcanic activity {vnum}: {e}")

    def get_activity_as_geojson(self):
        """Returns every currently-tracked volcano as GeoJSON -- no query-parameter
        filters (unlike the old VEI/significant/date-code triple): GVP's weekly report
        and HANS's elevated list are both already curated, so there's nothing left to
        filter client-side. is_new is derived here (not stored as a raw boolean) so the
        frontend's New-vs-Continuing icon selection never has to re-parse activity_type
        itself. Volcanoes with no coordinate yet recorded (a HANS-elevated volcano never
        once seen in any GVP report) are excluded -- nothing to plot."""
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(VolcanicActivity.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "vnum",
                VolcanicActivity.vnum,
                "name",
                VolcanicActivity.name,
                "country",
                VolcanicActivity.country,
                "activity_type",
                VolcanicActivity.activity_type,
                "is_new",
                func.lower(VolcanicActivity.activity_type).like("new%"),
                "report_description",
                VolcanicActivity.report_description,
                "hans_color_code",
                VolcanicActivity.hans_color_code,
                "hans_alert_level",
                VolcanicActivity.hans_alert_level,
                "hans_notice_url",
                VolcanicActivity.hans_notice_url,
            ),
        )
        collection = as_feature_collection(feature)
        stmt = select(cast(collection, SqlText)).where(VolcanicActivity.geom.isnot(None))
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building volcanic activity GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def prune_expired_activity(self, expiry_days=14):
        """Removes volcanoes not seen (in either source) within expiry_days -- the
        volcanic-activity equivalent of prune_expired_storms/prune_aircraft_tracks,
        called only by Housekeeper, never by the collector itself."""
        from datetime import timedelta

        cutoff = func.now() - timedelta(days=expiry_days)
        try:
            with Session() as session:
                result = session.execute(
                    delete(VolcanicActivity).where(VolcanicActivity.last_seen_at < cutoff)
                )
                session.commit()
                if result.rowcount > 0:
                    logger.info(f"Pruned {result.rowcount} expired volcanic activity record(s).")
                return result.rowcount
        except Exception as e:
            logger.error(f"Error pruning expired volcanic activity: {e}")
            return 0


class FakeVolcanicActivityAdapter:
    """In-memory fake for volcanic_activity, matching VolcanicActivityAdapter's method
    contracts."""

    def __init__(self):
        self._activity: dict[str, dict] = {}

    def upsert_activity(
        self,
        vnum,
        name,
        country,
        lat,
        lon,
        activity_type,
        report_description,
        hans_color_code,
        hans_alert_level,
        hans_notice_url,
    ):
        from datetime import datetime, timezone

        existing = self._activity.get(vnum, {})
        self._activity[vnum] = {
            "vnum": vnum,
            "name": name if name is not None else existing.get("name"),
            "country": country if country is not None else existing.get("country"),
            "lat": lat if lat is not None else existing.get("lat"),
            "lon": lon if lon is not None else existing.get("lon"),
            "activity_type": activity_type if activity_type is not None else existing.get("activity_type"),
            "report_description": report_description
            if report_description is not None
            else existing.get("report_description"),
            "hans_color_code": hans_color_code if hans_color_code is not None else existing.get("hans_color_code"),
            "hans_alert_level": hans_alert_level
            if hans_alert_level is not None
            else existing.get("hans_alert_level"),
            "hans_notice_url": hans_notice_url if hans_notice_url is not None else existing.get("hans_notice_url"),
            "last_seen_at": datetime.now(timezone.utc),
        }

    def get_activity_as_geojson(self):
        features = []
        for v in self._activity.values():
            if v["lat"] is None or v["lon"] is None:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]},
                    "properties": {
                        "vnum": v["vnum"],
                        "name": v["name"],
                        "country": v["country"],
                        "activity_type": v["activity_type"],
                        "is_new": _is_new(v["activity_type"]),
                        "report_description": v["report_description"],
                        "hans_color_code": v["hans_color_code"],
                        "hans_alert_level": v["hans_alert_level"],
                        "hans_notice_url": v["hans_notice_url"],
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def prune_expired_activity(self, expiry_days=14):
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        expired = [vnum for vnum, v in self._activity.items() if v["last_seen_at"] < cutoff]
        for vnum in expired:
            del self._activity[vnum]
        return len(expired)
