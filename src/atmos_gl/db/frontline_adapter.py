import json
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import FrontlineSnapshot

logger = logging.getLogger(__name__)


def _as_dict(row) -> dict:
    return {"id": row.id, "created_at": row.created_at,
            "description": row.description, "geojson": row.geojson}


def _with_snapshot(geojson: dict, snapshot_id, created_at, description) -> str:
    """The stored FeatureCollection plus a top-level "snapshot" member (GeoJSON allows
    foreign members; MapLibre ignores it) -- which DeepState update the map shows."""
    return json.dumps({
        **geojson,
        "snapshot": {
            "id": snapshot_id,
            "created_at": created_at.isoformat(),
            "description": description,
        },
    })


class FrontlineAdapter:
    """Real adapter for DeepStateMap front-line snapshots, backed by SQLAlchemy."""

    def has_snapshot(self, snapshot_id: int) -> bool:
        with Session() as session:
            return session.get(FrontlineSnapshot, snapshot_id) is not None

    def save_snapshot(self, snapshot_id: int, created_at, description, geojson: dict) -> None:
        stmt = pg_insert(FrontlineSnapshot).values(
            id=snapshot_id, created_at=created_at, description=description, geojson=geojson,
        ).on_conflict_do_nothing(index_elements=[FrontlineSnapshot.id])
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_snapshot_at(self, at=None) -> dict | None:
        """The newest stored snapshot created at or before `at` (None: the newest of
        all), as {id, created_at, description, geojson}; None if there isn't one."""
        stmt = select(FrontlineSnapshot)
        if at is not None:
            stmt = stmt.where(FrontlineSnapshot.created_at <= at)
        stmt = stmt.order_by(FrontlineSnapshot.created_at.desc(), FrontlineSnapshot.id.desc()).limit(1)
        with Session() as session:
            row = session.execute(stmt).scalar_one_or_none()
            return _as_dict(row) if row is not None else None

    def get_latest_geojson(self) -> str:
        try:
            row = self.get_snapshot_at()
        except Exception as e:
            logger.error(f"Error building frontline GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION
        if row is None:
            return EMPTY_FEATURE_COLLECTION
        return _with_snapshot(row["geojson"], row["id"], row["created_at"], row["description"])


class FakeFrontlineAdapter:
    """In-memory fake matching FrontlineAdapter's method contracts."""

    def __init__(self):
        self._snapshots: dict[int, dict] = {}

    def has_snapshot(self, snapshot_id: int) -> bool:
        return snapshot_id in self._snapshots

    def save_snapshot(self, snapshot_id: int, created_at, description, geojson: dict) -> None:
        self._snapshots.setdefault(snapshot_id, {
            "id": snapshot_id, "created_at": created_at,
            "description": description, "geojson": geojson,
        })

    def get_snapshot_at(self, at=None) -> dict | None:
        rows = [r for r in self._snapshots.values() if at is None or r["created_at"] <= at]
        if not rows:
            return None
        return dict(max(rows, key=lambda r: (r["created_at"], r["id"])))

    def get_latest_geojson(self) -> str:
        row = self.get_snapshot_at()
        if row is None:
            return EMPTY_FEATURE_COLLECTION
        return _with_snapshot(row["geojson"], row["id"], row["created_at"], row["description"])
