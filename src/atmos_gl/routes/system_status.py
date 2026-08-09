#!/usr/bin/env python3
"""GET /api/system_status — infrastructure health for the Config UI's System Status
section (Global tab): is each backend service process alive, is the database
reachable, and is the data volume filling up. A different concern from
routes/status.py's /api/data_status (which reports data FRESHNESS per collector/layer,
not whether the underlying container/process is even running) -- this is what would
have surfaced the data_collector/housekeeper containers going down silently.

Service liveness is a heartbeat proxy, not a live Docker query: each of
data_collector/layer_builder/housekeeper writes its own coarse "I'm alive" row
(kind="service") every loop tick via ProcessStatusAdapter.record_process_run(), see
collectors/service.py's run(), layer_builder.py's start_scheduler(), and
housekeeper.py's run(). A dead container simply stops advancing its row, which reads
as "stale" and then "dead" here as its last heartbeat ages past this module's
thresholds -- no Docker socket access needed (map_api has none).
"""
import logging
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text

from atmos_gl.db.engine import Session
from atmos_gl.db.process_status_adapter import ProcessStatusAdapter
from atmos_gl.routes.auth import require_admin
from atmos_gl.routes.config import load_config

logger = logging.getLogger("atmos_gl.routes.system_status")
router = APIRouter(prefix="/api", tags=["System Status"])

# Each backend service's own loop tick (see the docstring above for where each is
# written) and how many missed ticks before a heartbeat reads as "stale" (still
# probably fine, e.g. a slow render backlog) vs "dead" (very likely the process/
# container is gone). Thresholds are absolute, not derived from live config (e.g.
# data_collector's own backfill_poll_seconds setting) -- simpler and more robust than
# coupling this page to a setting that can change independently, and the natural tick
# rates below are already generous multiples of each service's steady-state cadence.
SERVICE_HEARTBEATS = {
    "data_collector": {"display_name": "Data Collector", "warn_after_s": 180, "dead_after_s": 600},
    "layer_builder": {"display_name": "Layer Builder", "warn_after_s": 180, "dead_after_s": 900},
    "housekeeper": {"display_name": "Housekeeper", "warn_after_s": 7200, "dead_after_s": 21600},
}


def get_process_status_adapter() -> ProcessStatusAdapter:
    return ProcessStatusAdapter()


def _service_status(name: str, meta: dict, process_status_adapter: ProcessStatusAdapter) -> dict:
    row = process_status_adapter.get_process_status(name)
    last_seen = row.get("last_updated") if row else None
    if last_seen is None:
        return {
            "name": name,
            "display_name": meta["display_name"],
            "status": "unknown",
            "last_seen": None,
            "detail": "No heartbeat recorded yet.",
        }

    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age_s = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if age_s < meta["warn_after_s"]:
        status = "ok"
    elif age_s < meta["dead_after_s"]:
        status = "stale"
    else:
        status = "dead"

    return {
        "name": name,
        "display_name": meta["display_name"],
        "status": status,
        "last_seen": last_seen.isoformat(),
        "detail": None,
    }


def _database_status() -> dict:
    started = datetime.now(timezone.utc)
    try:
        with Session() as session:
            session.execute(text("SELECT 1"))
        latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        return {"ok": True, "latency_ms": round(latency_ms, 1), "error": None}
    except Exception as e:
        logger.warning(f"System Status: database ping failed: {e}")
        return {"ok": False, "latency_ms": None, "error": str(e)}


def _dir_size_bytes(path: str) -> int | None:
    """Recursive size of everything under `path` (rendered outputs + fieldstore .npz
    cache) -- distinct from shutil.disk_usage()'s used_bytes below, which is the whole
    filesystem/volume `data_dir` lives on (shared with the OS, other containers'
    layers, etc.), not just our own data. None on any read error (e.g. a permission
    issue mid-walk) rather than a partial, misleadingly-low total."""
    try:
        total = 0
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                elif entry.is_dir(follow_symlinks=False):
                    sub = _dir_size_bytes(entry.path)
                    if sub is None:
                        return None
                    total += sub
        return total
    except OSError as e:
        logger.warning(f"System Status: data dir size check failed for {path}: {e}")
        return None


def _disk_status() -> dict:
    config = load_config()
    workdir = config.get_setting("common", "workdir", ".")
    data_dir = f"{workdir}/data"
    try:
        total, used, free = shutil.disk_usage(data_dir)
        return {
            "path": data_dir,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent_used": round(100.0 * used / total, 1) if total else 0.0,
            "our_data_bytes": _dir_size_bytes(data_dir),
        }
    except OSError as e:
        logger.warning(f"System Status: disk usage check failed for {data_dir}: {e}")
        return {
            "path": data_dir,
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "percent_used": None,
            "our_data_bytes": None,
        }


@router.get("/system_status")
def get_system_status(
    process_status_adapter: ProcessStatusAdapter = Depends(get_process_status_adapter),
    admin: dict = Depends(require_admin),
):
    services = [
        _service_status(name, meta, process_status_adapter)
        for name, meta in SERVICE_HEARTBEATS.items()
    ]
    return {
        "status": "success",
        "data": {
            "services": services,
            "database": _database_status(),
            "disk": _disk_status(),
        },
    }
