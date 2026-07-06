#!/usr/bin/env python3
"""GET /api/data_status — collector + layer-task status for the Config UI's Data Status
tab. Constructs one lightweight, throwaway instance per collector/task class (same pattern
_drive()/_render_worker() already use) and calls its read-only data_status()/layer_status(),
never collect()/render(). Safe to call from map_api, which never runs collection itself —
that's the whole point of process_status being written by the orchestration layer and read
here independently.
"""
import logging

from fastapi import APIRouter, HTTPException, Depends

from worldmap.db.field_catalog_adapter import FieldCatalogAdapter
from worldmap.lib import fieldstore
from worldmap.routes.config import load_config

from worldmap.collectors import (
    COLLECTORS,
    CACHE_COLLECTORS,
    FIELD_COLLECTOR_CLASSES,
    EMBEDDABLE_COLLECTORS,
    resolve_embeddable,
)

from worldmap.layer_builder import TASK_CLASSES
from worldmap.tasks.common import MapData

logger = logging.getLogger("worldmap.routes.status")
router = APIRouter(prefix="/api", tags=["Data Status"])


def _serialize(status: dict) -> dict:
    """JSON-safe copy of a data_status()/layer_status() dict: datetimes -> ISO 8601."""
    out = dict(status)
    for key in ("last_updated", "next_update"):
        v = out.get(key)
        if v is not None:
            out[key] = v.isoformat()
    return out


# --- Class-registry providers (architecture review candidate "Give routers the seam
# the Fakes are waiting for" -- the deferred status.py follow-on). This route's real
# untestability was never the one FieldCatalogAdapter it constructs (fieldstore.get_store
# freezes as a process-wide singleton on first use, so overriding that adapter here
# wouldn't reliably reach it in a test) -- it's the 23 real collector/task classes across
# 5 registries. Injecting the registries lets a test substitute a handful of stub classes
# and exercise this route's OWN logic (iteration, per-class exception swallowing,
# _serialize(), the final envelope) without touching a real DB, config, or the singleton.


def get_collector_classes():
    return COLLECTORS


def get_cache_collector_classes():
    return CACHE_COLLECTORS


def get_field_collector_classes():
    return FIELD_COLLECTOR_CLASSES


def get_embeddable_collector_classes():
    """Pre-resolves EMBEDDABLE_COLLECTORS into a plain list of classes, skipping any whose
    optional dependency isn't installed -- same per-name tolerance resolve_embeddable
    already gives, just resolved once here instead of inline in the route's loop."""
    return [
        cls
        for cls in (resolve_embeddable(name) for name in EMBEDDABLE_COLLECTORS)
        if cls is not None
    ]


def get_task_classes():
    return TASK_CLASSES


@router.get("/data_status")
def get_data_status(
    collector_classes=Depends(get_collector_classes),
    cache_collector_classes=Depends(get_cache_collector_classes),
    field_collector_classes=Depends(get_field_collector_classes),
    embeddable_collector_classes=Depends(get_embeddable_collector_classes),
    task_classes=Depends(get_task_classes),
):
    try:
        config = load_config()
        workdir = config.get_setting("common", "workdir", ".")
        store = fieldstore.get_store(workdir, field_catalog_adapter=FieldCatalogAdapter())

        collectors = []
        for CollectorCls in (*collector_classes, *cache_collector_classes):
            try:
                collectors.append(_serialize(CollectorCls(config).data_status()))
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in field_collector_classes:
            try:
                collectors.append(
                    _serialize(CollectorCls(config, store).data_status())
                )
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in embeddable_collector_classes:
            try:
                collectors.append(
                    _serialize(CollectorCls(config.config_path).data_status())
                )
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        layers = []
        map_data = MapData(config)
        for section, TaskCls in task_classes.items():
            try:
                layers.append(_serialize(TaskCls(config, map_data).layer_status()))
            except Exception as e:
                logger.error(f"layer_status failed for {section}: {e}")

        return {"status": "success", "data": {"collectors": collectors, "layers": layers}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
