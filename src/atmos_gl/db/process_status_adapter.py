from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.models import ProcessStatus


def _row_to_dict(row: ProcessStatus) -> dict:
    return {
        "name": row.name,
        "kind": row.kind,
        "last_updated": row.last_updated,
        "last_error": row.last_error,
        "status": row.status,
        "started_at": row.started_at,
        "updated_at": row.updated_at,
        "health": row.health,
        "health_detail": row.health_detail,
        "health_at": row.health_at,
    }


class ProcessStatusAdapter:
    """Real adapter for process_status, backed by SQLAlchemy.

    On success, last_updated advances to now() and last_error clears; on failure,
    last_updated is left untouched (still reflects the last GOOD run) and last_error
    records what went wrong. Mirrors the exact CASE-based upsert semantics the old
    Database.record_process_run() used.

    status/started_at track whether a run is CURRENTLY in progress -- needed because
    data_collector and map_api (which serves the Data Status UI) are separate
    processes, so an in-memory "I'm running" flag in the collector process wouldn't be
    visible to the process answering the status API. record_process_start() marks
    status="running" without touching last_updated/last_error (so freshness isn't
    faked while work is still in flight); record_process_run() clears started_at back
    to NULL on completion, since it's only meaningful while status is "running".
    """

    def record_process_start(self, name, kind):
        stmt = pg_insert(ProcessStatus).values(
            name=name,
            kind=kind,
            status="running",
            started_at=func.now(),
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProcessStatus.name],
            set_={
                "kind": stmt.excluded.kind,
                "status": "running",
                "started_at": func.now(),
                "updated_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def record_process_run(self, name, kind, success, error=None):
        stmt = pg_insert(ProcessStatus).values(
            name=name,
            kind=kind,
            last_updated=func.now() if success else None,
            last_error=error,
            status="success" if success else "failed",
            started_at=None,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProcessStatus.name],
            set_={
                "kind": stmt.excluded.kind,
                "last_updated": case(
                    (success, func.now()), else_=ProcessStatus.last_updated
                ),
                "last_error": None if success else stmt.excluded.last_error,
                "status": "success" if success else "failed",
                "started_at": None,
                "updated_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def record_health(self, name, kind, health, detail=None):
        """Sets (or, with health=None, explicitly clears) a transient
        upstream-condition signal -- e.g. "rate_limited"/"blocked" -- independent of
        record_process_run()'s pass/fail tracking: a handful of throttled requests
        doesn't mean the collector's whole run failed (AircraftCollector/
        GlobalSampleScheduler are explicitly designed to tolerate a rejected request as
        normal). Most callers never need to explicitly clear it -- an unrenewed
        condition just decays back to "ok" once lib/data_status.py's
        read_health_status() ttl_s passes, the same "assume recovered" shape
        freshness_percent already uses for staleness. Deliberately NOT touched by
        record_process_start()/record_process_run() -- only this method ever writes
        these three columns."""
        stmt = pg_insert(ProcessStatus).values(
            name=name,
            kind=kind,
            health=health,
            health_detail=detail,
            health_at=func.now(),
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProcessStatus.name],
            set_={
                "kind": stmt.excluded.kind,
                "health": stmt.excluded.health,
                "health_detail": stmt.excluded.health_detail,
                "health_at": func.now(),
                "updated_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_process_status(self, name):
        with Session() as session:
            row = session.get(ProcessStatus, name)
            return _row_to_dict(row) if row else None

    def get_all_process_status(self):
        with Session() as session:
            rows = session.scalars(select(ProcessStatus)).all()
            return {row.name: _row_to_dict(row) for row in rows}


class FakeProcessStatusAdapter:
    """In-memory fake for process_status, matching ProcessStatusAdapter's method contracts."""

    def __init__(self):
        self._rows: dict[str, dict] = {}

    def record_process_start(self, name, kind):
        existing = self._rows.get(name)
        now = datetime.now(timezone.utc)
        self._rows[name] = {
            "name": name,
            "kind": kind,
            "last_updated": existing["last_updated"] if existing else None,
            "last_error": existing["last_error"] if existing else None,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            # health/health_detail/health_at: untouched by this method, exactly like
            # the real adapter's upsert -- carried over from any existing row rather
            # than being dropped when this dict is rebuilt from scratch.
            "health": existing["health"] if existing else None,
            "health_detail": existing["health_detail"] if existing else None,
            "health_at": existing["health_at"] if existing else None,
        }

    def record_process_run(self, name, kind, success, error=None):
        existing = self._rows.get(name)
        now = datetime.now(timezone.utc)
        last_updated = now if success else (existing["last_updated"] if existing else None)
        last_error = None if success else error
        self._rows[name] = {
            "name": name,
            "kind": kind,
            "last_updated": last_updated,
            "last_error": last_error,
            "status": "success" if success else "failed",
            "started_at": None,
            "updated_at": now,
            # See record_process_start()'s identical comment -- health fields are
            # never touched by this method either.
            "health": existing["health"] if existing else None,
            "health_detail": existing["health_detail"] if existing else None,
            "health_at": existing["health_at"] if existing else None,
        }

    def record_health(self, name, kind, health, detail=None):
        existing = self._rows.get(name)
        now = datetime.now(timezone.utc)
        row = dict(existing) if existing else {
            "name": name, "kind": kind, "last_updated": None,
            "last_error": None, "status": None, "started_at": None,
        }
        row["kind"] = kind
        row["health"] = health
        row["health_detail"] = detail
        row["health_at"] = now
        row["updated_at"] = now
        self._rows[name] = row

    def get_process_status(self, name):
        row = self._rows.get(name)
        return dict(row) if row else None

    def get_all_process_status(self):
        return {name: dict(row) for name, row in self._rows.items()}
