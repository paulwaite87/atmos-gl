from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.models import ViewportState

CURRENT_ID = "current"


class ViewportAdapter:
    """Real adapter for viewport_state, backed by SQLAlchemy.

    Single-row table (see ViewportState's docstring for why this exists): map_api's
    POST /api/viewport writes here on every reported map move, and data_collector's
    LightningCollector (a separate container) reads it back each scan to weight its
    grid-point queue toward wherever the map is currently pointed.
    """

    def update_viewport(self, lat: float, lon: float, zoom: float | None = None) -> None:
        stmt = pg_insert(ViewportState).values(
            id=CURRENT_ID,
            lat=lat,
            lon=lon,
            zoom=zoom,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ViewportState.id],
            set_={
                "lat": stmt.excluded.lat,
                "lon": stmt.excluded.lon,
                "zoom": stmt.excluded.zoom,
                "updated_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_viewport(self) -> dict | None:
        stmt = select(ViewportState).where(ViewportState.id == CURRENT_ID)
        with Session() as session:
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            return {
                "lat": row.lat,
                "lon": row.lon,
                "zoom": row.zoom,
                "updated_at": row.updated_at,
            }


class FakeViewportAdapter:
    """In-memory fake for viewport_state, matching ViewportAdapter's method contracts."""

    def __init__(self):
        self._viewport: dict | None = None

    def update_viewport(self, lat: float, lon: float, zoom: float | None = None) -> None:
        self._viewport = {
            "lat": lat,
            "lon": lon,
            "zoom": zoom,
            "updated_at": datetime.now(timezone.utc),
        }

    def get_viewport(self) -> dict | None:
        return dict(self._viewport) if self._viewport else None
