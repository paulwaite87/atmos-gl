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

from atmos_gl.db.field_catalog_adapter import FieldCatalogAdapter
from atmos_gl.lib import fieldstore
from atmos_gl.routes.config import load_config

from atmos_gl.collectors import (
    COLLECTORS,
    CACHE_COLLECTORS,
    FIELD_COLLECTOR_CLASSES,
    EMBEDDABLE_COLLECTORS,
    resolve_embeddable,
)

from atmos_gl.layer_builder import TASK_CLASSES
from atmos_gl.tasks.common import MapData
from atmos_gl.routes.field_specs import section_label

logger = logging.getLogger("atmos_gl.routes.status")
router = APIRouter(prefix="/api", tags=["Data Status"])

# status_name values for FieldCollectorBase subclasses ("gfs_atmos", "gfs_waves",
# "rtofs_currents") aren't real config sections, so section_label()'s SECTION_LABELS
# lookup (and its .title() fallback) doesn't know GFS/RTOFS should stay acronyms --
# override here rather than teaching SECTION_LABELS about identities that aren't
# sections at all.
_STATUS_NAME_LABELS = {
    "gfs_atmos": "GFS Atmos",
    "gfs_waves": "GFS Waves",
    "rtofs_currents": "RTOFS Currents",
}


def _display_name(key: str) -> str:
    """Friendly name for a Data Status row -- matches the Show tab's wording exactly
    for anything that's a real config section (via section_label()); the 3 field
    collectors' status_name-only identities get their own override above."""
    return _STATUS_NAME_LABELS.get(key, section_label(key))


def _serialize(status: dict, channel_key: str | None, channel_enabled: dict) -> dict:
    """JSON-safe copy of a data_status()/layer_status() dict: datetimes -> ISO 8601.

    channel_key (data_collector.channel_enabled's key, e.g. "gfs_atmos") and the
    channel's current on/off state are attached here rather than inside
    data_status()/layer_status() themselves -- it's a Data Status UI concern (which
    row to gray out, and what the opt-out switch should show), not something those
    methods otherwise need to know. channel_key is None for a row that isn't gated by
    channel_enabled at all (e.g. storms, markers, shipping/lightning -- see
    CollectorBase.channel_key); channel_on is then also None (not applicable) rather
    than True, so the frontend can distinguish "no switch" from "switch, currently on".
    Defaults to True (matches the wired-in gating's own default) when a key hasn't
    been written to channel_enabled yet, e.g. right after upgrading to this feature.

    Also adds display_name, a friendly version of `name` for the UI -- `name` itself
    is left untouched (some existing tests assert it verbatim, and it's still the
    right value for anything keying off the raw section/status_name)."""
    out = dict(status)
    for key in ("last_updated", "next_update"):
        v = out.get(key)
        if v is not None:
            out[key] = v.isoformat()
    out["channel_key"] = channel_key
    out["channel_on"] = channel_enabled.get(channel_key, True) if channel_key else None
    out["display_name"] = _display_name(out["name"])
    return out


def _build_layer_channel_keys(field_collector_classes, cache_collector_classes) -> dict:
    """Maps a layer's TASK_CLASSES section name (e.g. "isobars") to the channel_key
    that feeds it (e.g. "gfs_atmos"), so the Data Status UI can gray out every layer a
    disabled channel backs. Derived from the collector classes' own `products`/
    `channel_key` rather than hand-duplicated, so the two can't drift apart."""
    mapping = {}
    for CollectorCls in field_collector_classes:
        if getattr(CollectorCls, "channel_key", None):
            for product_name in CollectorCls.products:
                mapping[product_name] = getattr(CollectorCls, "channel_key", None)
    for CollectorCls in cache_collector_classes:
        if getattr(CollectorCls, "channel_key", None):
            mapping[CollectorCls.section] = getattr(CollectorCls, "channel_key", None)
    return mapping


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
        channel_enabled = config.get_setting("data_collector", "channel_enabled", {}) or {}

        collectors = []
        for CollectorCls in (*collector_classes, *cache_collector_classes):
            try:
                channel_key = getattr(CollectorCls, "channel_key", None)
                status = CollectorCls(config).data_status()
                collectors.append(_serialize(status, channel_key, channel_enabled))
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in field_collector_classes:
            try:
                channel_key = getattr(CollectorCls, "channel_key", None)
                status = CollectorCls(config, store).data_status()
                collectors.append(_serialize(status, channel_key, channel_enabled))
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        for CollectorCls in embeddable_collector_classes:
            try:
                channel_key = getattr(CollectorCls, "channel_key", None)
                status = CollectorCls(config.config_path).data_status()
                collectors.append(_serialize(status, channel_key, channel_enabled))
            except Exception as e:
                logger.error(f"data_status failed for {CollectorCls.__name__}: {e}")

        layer_channel_keys = _build_layer_channel_keys(
            field_collector_classes, cache_collector_classes
        )
        layers = []
        map_data = MapData(config)
        for section, TaskCls in task_classes.items():
            try:
                layers.append(
                    _serialize(
                        TaskCls(config, map_data).layer_status(),
                        layer_channel_keys.get(section),
                        channel_enabled,
                    )
                )
            except Exception as e:
                logger.error(f"layer_status failed for {section}: {e}")

        # Group the background-service collectors (satellites_collector,
        # shipping_collector, lightning_collector -- named for the service they run,
        # not the data they gather) after every real data-source row, rather than
        # interleaved with them in whatever order the registries happen to define.
        # Stable sort preserves each group's existing relative order.
        collectors.sort(key=lambda c: c["name"].endswith("_collector"))

        return {"status": "success", "data": {"collectors": collectors, "layers": layers}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data_status/channel_enabled/{channel_key}")
def set_channel_enabled(channel_key: str, payload: dict):
    """Flip one data_collector.channel_enabled[channel_key] and save immediately --
    deliberately its own tiny endpoint rather than routing through /api/config's POST
    (which replaces the ENTIRE config from the client's masterConfigCache, so a
    partial payload would wipe every other section). The Data Status tab is read-only
    and independent of that big form/masterConfigCache by design (see config.html);
    opting a channel in/out is an operational action that should take effect the
    instant it's clicked, not wait for a separate batch "Save"."""
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled must be a boolean")

    config = load_config()
    config.config.setdefault("data_collector", {}).setdefault("channel_enabled", {})
    config.config["data_collector"]["channel_enabled"][channel_key] = enabled
    config.save()
    return {"status": "success", "channel_key": channel_key, "enabled": enabled}
