import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from worldmap.db.engine import Session
from worldmap.db.models import Satellite

logger = logging.getLogger(__name__)


class SatelliteAdapter:
    """Real adapter for satellites, backed by SQLAlchemy."""

    def update_satellite(self, norad_id, name, omm, epoch_iso):
        stmt = pg_insert(Satellite).values(
            norad_id=norad_id,
            name=name,
            omm=omm,
            epoch=epoch_iso,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Satellite.norad_id],
            set_={
                "name": stmt.excluded.name,
                "omm": stmt.excluded.omm,
                "epoch": stmt.excluded.epoch,
                "updated_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_satellites_by_names(self, names):
        if not names:
            return []
        stmt = select(
            Satellite.norad_id, Satellite.name, Satellite.omm, Satellite.epoch
        ).where(Satellite.name.in_(list(names)))
        with Session() as session:
            rows = session.execute(stmt).all()
            return [
                {"norad_id": r.norad_id, "name": r.name, "omm": r.omm, "epoch": r.epoch}
                for r in rows
            ]


class FakeSatelliteAdapter:
    """In-memory fake for satellites, matching SatelliteAdapter's method contracts."""

    def __init__(self):
        self._satellites: dict[int, dict] = {}

    def update_satellite(self, norad_id, name, omm, epoch_iso):
        self._satellites[norad_id] = {
            "norad_id": norad_id,
            "name": name,
            "omm": omm,
            "epoch": epoch_iso,
        }

    def get_satellites_by_names(self, names):
        if not names:
            return []
        names = set(names)
        return [dict(s) for s in self._satellites.values() if s["name"] in names]
