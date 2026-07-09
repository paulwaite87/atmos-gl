#!/usr/bin/env python3
"""Collectors: pure data sources that keep the backend warm, independent of any layer's
frontend `enabled` flag.

Three families share one scheduling contract (CollectorBase: is_stale + has_new_data +
collect) and one driver loop (_drive), so adding a source is "one file + one registry
entry", not a new branch in a monolith:

Synchronous event feeds  (COLLECTORS)        — write straight to the DB
--------------------------------------------------------------------------
  quakes     — USGS earthquake CSV, runs_per_day=24 (every ~hour)
  storms     — NHC/JTWC ATCF b/a-deck files, runs_per_day=8
  volcanoes  — NOAA HazEL REST API, runs_per_day=1
  satellites — CelesTrak OMM JSON, period derived from update_hours (default 12h)
  markers    — LOCAL markers.geojson -> DB 'markers' table (mtime-gated, not remote)

Synchronous file caches  (CACHE_COLLECTORS)  — write an image/netCDF under {workdir}/data
--------------------------------------------------------------------------
  sst        — OISST yearly netCDF (SstCollector, collectors/sst.py)
  clouds     — NASA GIBS global cloud image (CloudsCollector, collectors/clouds.py)

  These are single fields (one daily netCDF / one global image), not per-forecast-hour
  products, so they live as file caches rather than fieldstore rows. The layer updaters
  render from the cache; this package only keeps the cache fresh.

Field collectors  (FIELD_COLLECTOR_CLASSES)  — fieldstore-backed, per-forecast-hour
--------------------------------------------------------------------------
  gfs atmos/waves, rtofs currents (FieldCollectorBase subclasses, collectors/gfs_atmos.py,
  gfs_waves.py, rtofs_currents.py). Driven per-cycle by CollectorService, sharing one
  CycleContext baseline probe. Canonical list, imported by both service.py and
  routes/status.py so a new field collector can't drift between the two.

Async collectors  (EMBEDDABLE_COLLECTORS)    — persistent coroutines
--------------------------------------------------------------------------
  shipping   — AIS WebSocket stream   (ShippingCollector, collectors/shipping.py)
  lightning  — OpenWeather REST        (LightningCollector, collectors/lightning.py)

  Run in-process as supervised asyncio tasks (or as standalone Docker services). They
  keep their own `enabled` kill-switch since they're API-key gated and user-specific.
  Resolved lazily (resolve_embeddable) so a missing optional dependency for one can't
  break import of this module.

Collection is UNCONDITIONAL of any layer `enabled` flag: `enabled` is a FRONTEND
visibility control, and the data must already be present so a layer renders the moment a
user toggles it on. (The async pair is the deliberate exception: key-gated + enabled.)
"""

import time
import logging

from .quakes import QuakeCollector
from .storms import StormsCollector
from .volcanoes import VolcanoesCollector
from .satellites import SatellitesCollector
from .markers_sync import MarkersSyncCollector
from atmos_gl.collectors.sst import SstCollector
from atmos_gl.collectors.clouds import CloudsCollector
from atmos_gl.collectors.gfs_atmos import GfsAtmosCollector
from atmos_gl.collectors.gfs_waves import GfsWavesCollector
from atmos_gl.collectors.rtofs_currents import RtofsCurrentsCollector
from atmos_gl.db.process_status_adapter import ProcessStatusAdapter

logger = logging.getLogger(__name__)

# Synchronous periodic collectors that write to the DB, driven by collect_event_feeds().
COLLECTORS = (
    QuakeCollector,
    StormsCollector,
    VolcanoesCollector,
    SatellitesCollector,
    MarkersSyncCollector,
)

# Synchronous file-cache collectors (image/netCDF under {workdir}/data), driven by
# collect_file_caches(). Same contract as COLLECTORS; separate registry only because the
# caller wants to schedule/observe the two families independently.
CACHE_COLLECTORS = (
    SstCollector,
    CloudsCollector,
)

# Field collectors (fieldstore-backed, FieldCollectorBase), driven per-cycle by
# CollectorService._collect_fields()/drain_backfill(). Canonical list — both
# collectors/service.py and routes/status.py import this so a new field collector can't
# run in one place while silently missing from the other (previously two hand-copied
# tuples that could drift).
FIELD_COLLECTOR_CLASSES = (GfsAtmosCollector, GfsWavesCollector, RtofsCurrentsCollector)

# Async collectors (AsyncCollectorBase persistent coroutines) that can run in-process,
# keyed by config-section name. Resolved lazily via resolve_embeddable() (importlib) so a
# missing optional dependency for one collector can't break import of this module.
EMBEDDABLE_COLLECTORS = {
    "shipping_collector": ("atmos_gl.collectors.shipping", "ShippingCollector"),
    "lightning_collector": ("atmos_gl.collectors.lightning", "LightningCollector"),
}


def resolve_embeddable(name):
    spec = EMBEDDABLE_COLLECTORS.get(name)
    if spec is None:
        return None
    import importlib

    module_name, cls_name = spec
    return getattr(importlib.import_module(module_name), cls_name)


def _drive(collectors, config, last_runs: dict) -> None:
    """Run each collector in `collectors`, subject to per-collector scheduling.

    The single loop shared by every synchronous collector family. Per collector:
      * is_stale()      — gates on the collector's own runs_per_day / period_s, so a
                          fast feed (quakes, 24/day) and a slow one (volcanoes, 1/day)
                          share this loop without the loop knowing their cadence.
      * has_new_data()  — cheap HEAD/ETag (or file-age) pre-check; on unchanged remote we
                          record the timestamp and skip the full fetch.
      * collect()       — full fetch, called only when stale AND changed.

    last_runs is mutated in-place: {section -> time.monotonic() of last check}. The
    timestamp is updated on BOTH "collected" and "unchanged" outcomes so each collector's
    period counts down correctly between checks. One collector failing is logged and
    skipped; it never aborts the others.

    Also records process_status for the Data Status UI (process_status_adapter.record_process_run): a
    successful check OR collect both count as "success" (last_updated advances) — an
    unchanged-but-verified remote is not staleness, it's the collector doing its job. A
    not-yet-due collector (is_stale() False) records nothing; it wasn't checked at all.

    record_process_start() is called right before feed.collect() -- the potentially
    slow part (a multi-hundred-MB download, for example) -- so the Data Status UI can
    show "running" instead of sitting on a stale reading for however long collect()
    takes. data_collector and map_api (which serves that UI) are separate processes,
    so this has to go through process_status (the shared DB), not an in-memory flag.
    """
    now = time.monotonic()
    process_status_adapter = ProcessStatusAdapter()
    for CollectorCls in collectors:
        key = CollectorCls.section
        try:
            feed = CollectorCls(config)
            if not feed.is_stale(last_runs.get(key)):
                logger.debug(
                    f"{key}: not yet due "
                    f"(period {feed.period_s:.0f}s, "
                    f"next in {feed.period_s - (time.monotonic() - (last_runs.get(key) or 0)):.0f}s)."
                )
                continue
            if not feed.has_new_data():
                last_runs[key] = now
                process_status_adapter.record_process_run(key, "collector", success=True)
                continue
            logger.info(f"{key}: collecting...")
            process_status_adapter.record_process_start(key, "collector")
            feed.collect()
            last_runs[key] = now
            process_status_adapter.record_process_run(key, "collector", success=True)
        except Exception as exc:
            logger.error(
                f"collector {CollectorCls.__name__} failed: {exc}", exc_info=True
            )
            process_status_adapter.record_process_run(
                key, "collector", success=False, error=str(exc)
            )


def collect_event_feeds(config, last_runs: dict) -> None:
    """Drive the DB-writing event feeds (quakes, storms, volcanoes, satellites, markers).

    Collection is UNCONDITIONAL of the layer's `enabled` flag; see module docstring.
    """
    _drive(COLLECTORS, config, last_runs)


def collect_file_caches(config, last_runs: dict) -> None:
    """Drive the file-cache collectors (sst, clouds).

    Same scheduling contract as collect_event_feeds; separate last_runs dict so the two
    families schedule independently. Collection is UNCONDITIONAL of `enabled`.
    """
    _drive(CACHE_COLLECTORS, config, last_runs)
