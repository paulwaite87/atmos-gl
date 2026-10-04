import json
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import FrontlineSnapshot

logger = logging.getLogger(__name__)


def _as_dict(row) -> dict:
    return {"id": row.id, "created_at": row.created_at, "source_updated_at": row.source_updated_at,
            "description": row.description, "description_segments": row.description_segments,
            "geojson": row.geojson}


def _with_snapshot(row: dict) -> str:
    """The stored FeatureCollection plus a top-level "snapshot" member (GeoJSON allows
    foreign members; MapLibre ignores it) -- which DeepState update the map shows."""
    return json.dumps({
        **row["geojson"],
        "snapshot": {
            "id": row["id"],
            "created_at": row["created_at"].isoformat(),
            "description": row["description"],
            "description_segments": row["description_segments"],
        },
    })


class FrontlineAdapter:
    """Real adapter for DeepStateMap front-line snapshots, backed by SQLAlchemy."""

    def has_snapshot(self, snapshot_id: int) -> bool:
        with Session() as session:
            return session.get(FrontlineSnapshot, snapshot_id) is not None

    def get_source_updated_at(self, snapshot_id: int):
        """DeepState's updatedAt as stored with the snapshot; None if the snapshot isn't
        stored or predates the column."""
        with Session() as session:
            return session.execute(
                select(FrontlineSnapshot.source_updated_at).where(FrontlineSnapshot.id == snapshot_id)
            ).scalar()

    def save_snapshot(
        self, snapshot_id: int, created_at, description, geojson: dict,
        description_segments=None, source_updated_at=None,
    ) -> None:
        """Stores the snapshot, replacing any stored copy of the same update (a
        re-fetch after DeepState edited it)."""
        values = dict(
            created_at=created_at, description=description, geojson=geojson,
            description_segments=description_segments, source_updated_at=source_updated_at,
        )
        stmt = pg_insert(FrontlineSnapshot).values(id=snapshot_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[FrontlineSnapshot.id], set_=values)
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
        return _with_snapshot(row)

    def prune_older_than(self, keep_days: float) -> int:
        """Deletes snapshots created more than keep_days before the newest one, except
        the newest of those -- the update that was current at the cutoff, so a
        gains/losses baseline that far back survives. Measured from the newest snapshot,
        not from now, matching the gains/losses windows. Returns the number deleted."""
        with Session() as session:
            latest = session.execute(select(func.max(FrontlineSnapshot.created_at))).scalar()
            if latest is None:
                return 0
            cutoff = latest - timedelta(days=keep_days)
            anchor = session.execute(
                select(FrontlineSnapshot.id)
                .where(FrontlineSnapshot.created_at <= cutoff)
                .order_by(FrontlineSnapshot.created_at.desc(), FrontlineSnapshot.id.desc())
                .limit(1)
            ).scalar()
            if anchor is None:
                return 0
            result = session.execute(
                delete(FrontlineSnapshot).where(
                    FrontlineSnapshot.created_at <= cutoff, FrontlineSnapshot.id != anchor,
                )
            )
            session.commit()
            return result.rowcount


class FakeFrontlineAdapter:
    """In-memory fake matching FrontlineAdapter's method contracts."""

    def __init__(self):
        self._snapshots: dict[int, dict] = {}

    def has_snapshot(self, snapshot_id: int) -> bool:
        return snapshot_id in self._snapshots

    def get_source_updated_at(self, snapshot_id: int):
        return (self._snapshots.get(snapshot_id) or {}).get("source_updated_at")

    def save_snapshot(
        self, snapshot_id: int, created_at, description, geojson: dict,
        description_segments=None, source_updated_at=None,
    ) -> None:
        self._snapshots[snapshot_id] = {
            "id": snapshot_id, "created_at": created_at, "source_updated_at": source_updated_at,
            "description": description, "description_segments": description_segments,
            "geojson": geojson,
        }

    def get_snapshot_at(self, at=None) -> dict | None:
        rows = [r for r in self._snapshots.values() if at is None or r["created_at"] <= at]
        if not rows:
            return None
        return dict(max(rows, key=lambda r: (r["created_at"], r["id"])))

    def get_latest_geojson(self) -> str:
        row = self.get_snapshot_at()
        if row is None:
            return EMPTY_FEATURE_COLLECTION
        return _with_snapshot(row)

    def prune_older_than(self, keep_days: float) -> int:
        """Mirrors FrontlineAdapter.prune_older_than."""
        if not self._snapshots:
            return 0
        cutoff = max(r["created_at"] for r in self._snapshots.values()) - timedelta(days=keep_days)
        old = [r for r in self._snapshots.values() if r["created_at"] <= cutoff]
        if not old:
            return 0
        anchor = max(old, key=lambda r: (r["created_at"], r["id"]))["id"]
        doomed = [r["id"] for r in old if r["id"] != anchor]
        for snapshot_id in doomed:
            del self._snapshots[snapshot_id]
        return len(doomed)
