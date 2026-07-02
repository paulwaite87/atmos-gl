#!/usr/bin/env python3
"""GET /api/data_status — collector + layer-task status for the Config UI's Data Status
tab. Constructs one lightweight, throwaway instance per collector/task class (same pattern
_drive()/_render_worker() already use) and calls its read-only data_status()/layer_status(),
never collect()/render(). Safe to call from map_api, which never runs collection itself —
that's the whole point of process_status being written by the orchestration layer and read
here independently.
"""
import logging

from fastapi import APIRouter, HTTPException

from worldmap.lib.db import Database
from worldmap.lib import fieldstore
from worldmap.routes.config import load_config

from worldmap.collectors import COLLECTORS, CACHE_COLLECTORS
from worldmap.collectors.gfs_atmos import GfsAtmosCollector
from worldmap.collectors.gfs_waves import GfsWavesCollector
from worldmap.collectors.rtofs_currents import RtofsCurrentsCollector
from worldmap.collectors.shipping import ShippingCollector
from worldmap.collectors.lightning import LightningCollector

from worldmap.layer_builder import TASK_CLASSES
from worldmap.tasks.common import MapData

logger = logging.getLogger("worldmap.routes.status")
router = APIRouter(prefix="/api", tags=["Data Status"])

_FIELD_COLLECTOR_CLASSES = (GfsAtmosCollector, GfsWavesCollector, RtofsCurrentsCollector)
_ASYNC_COLLECTOR_CLASSES = (ShippingCollector, LightningCollector)


def _serialize(status: dict) -> dict:
    """JSON-safe copy of a data_status()/layer_status() dict: datetimes -> ISO 8601."""
    out = dict(status)
    for key in ("last_updated", "next_update"):
        v = out.get(key)
        if v is not None:
            out[key] = v.isoformat()
    return out


@router.get("/data_status")
def get_data_status():
    try:
        config = load_config()
        db = Database()
        workdir = config.get_setting("common", "workdir", ".")
        store = fieldstore.get_store(workdir, db=db)

        collectors = []
        for CollectorCls in (*COLLECTORS, *CACHE_COLLECTORS):
            try:
                collectors.append(_serialize(CollectorCls(config, db).data_status()))
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in _FIELD_COLLECTOR_CLASSES:
            try:
                collectors.append(
                    _serialize(CollectorCls(config, db, store).data_status())
                )
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in _ASYNC_COLLECTOR_CLASSES:
            try:
                collectors.append(
                    _serialize(CollectorCls(config.config_path).data_status())
                )
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        layers = []
        map_data = MapData(config)
        for section, TaskCls in TASK_CLASSES.items():
            try:
                layers.append(_serialize(TaskCls(config, map_data).layer_status()))
            except Exception as e:
                logger.error(f"layer_status failed for {section}: {e}")

        return {"status": "success", "data": {"collectors": collectors, "layers": layers}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
