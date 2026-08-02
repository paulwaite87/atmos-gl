#!/usr/bin/env python3
"""Collectors: pure data sources that keep the backend warm, independent of any layer's
frontend `enabled` flag.

Three families share one scheduling contract (CollectorBase: is_stale + has_new_data +
collect) and one driver loop (_drive), so adding a source is "one file + one registry
entry", not a new branch in a monolith:

Synchronous event feeds  (COLLECTORS)        — write straight to the DB
--------------------------------------------------------------------------
  quakes     — USGS earthquake CSV, runs_per_day=24 (every ~hour)
  storms     — NHC/JTWC ATCF b/a-deck files, runs_per_day=6
  volcanoes  — GVP Weekly Volcanic Activity Report (global) + USGS HANS (US enrichment),
               runs_per_day=24
  fires      — NASA FIRMS VIIRS_NOAA20_NRT active-fire CSV, runs_per_day=24 (every ~hour)
  satellites — CelesTrak OMM JSON, runs_per_day=6
  markers    — LOCAL markers.geojson -> DB 'markers' table (mtime-gated, not remote)

Synchronous file caches  (CACHE_COLLECTORS)  — write an image/netCDF under {workdir}/data
--------------------------------------------------------------------------
  sst        — OISST yearly netCDF (SstCollector, collectors/sst.py)
  clouds     — NASA GIBS global cloud image (CloudsCollector, collectors/clouds.py)
  greenhouse_gases — CAMS current CO2/CH4 forecast + CAMS EGG4 historical baseline
               (CamsGhgForecastCollector, CamsEgg4BaselineCollector, collectors/
               greenhouse_gases.py) -- two collectors sharing one settings section
               (via settings_section) but independently scheduled/reported, since one
               runs a normal periodic cadence and the other fetches once per baseline
               year then is done.
  air_quality — CAMS current PM2.5/PM10/smoke-AOD forecast (AirQualityCollector,
               collectors/air_quality.py) -- Absolute-only, single collector, no
               settings_section sharing.

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

import logging

from .quakes import QuakeCollector
from .storms import StormsCollector
from .volcanoes import VolcanicActivityCollector
from .fires import FiresCollector
from .satellites import SatellitesCollector
from .markers_sync import MarkersSyncCollector
from atmos_gl.collectors.sst import SstCollector
from atmos_gl.collectors.clouds import CloudsCollector
from atmos_gl.collectors.greenhouse_gases import (
    CamsEgg4BaselineCollector,
    CamsGhgForecastCollector,
)
from atmos_gl.collectors.air_quality import AirQualityCollector
from atmos_gl.collectors.gfs_atmos import GfsAtmosCollector
from atmos_gl.collectors.gfs_waves import GfsWavesCollector
from atmos_gl.collectors.rtofs_currents import RtofsCurrentsCollector
from atmos_gl.collectors.driving import EventFeedDriver

logger = logging.getLogger(__name__)

# Synchronous periodic collectors that write to the DB, driven by collect_event_feeds().
COLLECTORS = (
    QuakeCollector,
    StormsCollector,
    VolcanicActivityCollector,
    FiresCollector,
    SatellitesCollector,
    MarkersSyncCollector,
)

# Synchronous file-cache collectors (image/netCDF under {workdir}/data), driven by
# collect_file_caches(). Same contract as COLLECTORS; separate registry only because the
# caller wants to schedule/observe the two families independently.
CACHE_COLLECTORS = (
    SstCollector,
    CloudsCollector,
    CamsGhgForecastCollector,
    CamsEgg4BaselineCollector,
    AirQualityCollector,
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
    "flightradar_collector": ("atmos_gl.collectors.aircraft", "AircraftCollector"),
}


def resolve_embeddable(name):
    spec = EMBEDDABLE_COLLECTORS.get(name)
    if spec is None:
        return None
    import importlib

    module_name, cls_name = spec
    return getattr(importlib.import_module(module_name), cls_name)


def collect_event_feeds(config, last_runs: dict) -> None:
    """Drive the DB-writing event feeds (quakes, storms, volcanoes, satellites, markers).

    Collection is UNCONDITIONAL of the layer's `enabled` flag; see module docstring.
    See collectors/driving.py's EventFeedDriver for the shared scheduling contract
    (is_stale/has_new_data/collect, channel gating, process_status recording).
    """
    EventFeedDriver(config, last_runs).drive(COLLECTORS)


def collect_file_caches(config, last_runs: dict) -> None:
    """Drive the file-cache collectors (sst, clouds).

    Same scheduling contract as collect_event_feeds (EventFeedDriver); separate
    last_runs dict so the two families schedule independently. Collection is
    UNCONDITIONAL of `enabled`.
    """
    EventFeedDriver(config, last_runs).drive(CACHE_COLLECTORS)
