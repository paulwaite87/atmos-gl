import json
import logging

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.models import Volcano

logger = logging.getLogger(__name__)


class VolcanoAdapter:
    """Real adapter for volcanoes, backed by SQLAlchemy."""

    def update_volcano(self, v_id, name, lat, lon, vei, significant, date_code):
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = pg_insert(Volcano).values(
            id=v_id,
            name=name,
            lat=lat,
            lon=lon,
            vei=vei,
            significant=significant,
            erupt_date_code=date_code,
            geom=point,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Volcano.id],
            set_={
                "vei": stmt.excluded.vei,
                "significant": stmt.excluded.significant,
                "erupt_date_code": stmt.excluded.erupt_date_code,
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_volcanoes_as_geojson(self, vei_min, significant, date_codes):
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Volcano.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "name",
                Volcano.name,
                "vei",
                Volcano.vei,
                "code",
                Volcano.erupt_date_code,
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(func.jsonb_agg(feature), text("'[]'::jsonb")),
        )
        conditions = [
            Volcano.vei >= vei_min,
            Volcano.erupt_date_code.in_(list(date_codes)),
        ]
        if significant:
            conditions.append(Volcano.significant == True)  # noqa: E712
        stmt = select(cast(collection, SqlText)).where(*conditions)
        with Session() as session:
            result = session.scalar(stmt)
            return result if result is not None else '{"type":"FeatureCollection","features":[]}'


class FakeVolcanoAdapter:
    """In-memory fake for volcanoes, matching VolcanoAdapter's method contracts."""

    def __init__(self):
        self._volcanoes: dict[str, dict] = {}

    def update_volcano(self, v_id, name, lat, lon, vei, significant, date_code):
        existing = self._volcanoes.get(v_id)
        if existing is None:
            self._volcanoes[v_id] = {
                "id": v_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "vei": vei,
                "significant": significant,
                "erupt_date_code": date_code,
            }
            return
        # ON CONFLICT only updates vei/significant/erupt_date_code; name/lat/lon is immutable.
        existing["vei"] = vei
        existing["significant"] = significant
        existing["erupt_date_code"] = date_code

    def get_volcanoes_as_geojson(self, vei_min, significant, date_codes):
        date_codes = set(date_codes)
        features = []
        for v in self._volcanoes.values():
            if v["vei"] < vei_min:
                continue
            if significant and not v["significant"]:
                continue
            if v["erupt_date_code"] not in date_codes:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]},
                    "properties": {
                        "name": v["name"],
                        "vei": v["vei"],
                        "code": v["erupt_date_code"],
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})
