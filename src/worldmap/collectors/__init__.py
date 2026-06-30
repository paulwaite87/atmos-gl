#!/usr/bin/env python3
"""Event-feed collectors: pure data sources that write straight to the DB.

Each collector is a `CollectorBase` subclass responsible for ONE external source. The
data_collector drives them via `collect_event_feeds()`, which handles per-collector rate
limiting (runs_per_day) and cheap remote-freshness checks (HEAD/ETag) independently.

Collection is UNCONDITIONAL of any layer `enabled` flag: `enabled` is a frontend
visibility control, and the data must already be in the DB so a layer renders the moment
a user toggles it on. Collectors run on their own schedule whether their layer is shown
or not.

Synchronous collectors — driven by collect_event_feeds() in DataCollector
--------------------------------------------------------------------------
  quakes     — USGS earthquake CSV, runs_per_day=24 (every ~hour)
  storms     — NHC/JTWC ATCF b/a-deck files, runs_per_day=8
  volcanoes  — NOAA HazEL REST API, runs_per_day=1
  satellites — CelesTrak OMM JSON, period derived from update_hours (default 12h)
  markers    — LOCAL markers.geojson -> DB 'markers' table (mtime-gated, not a remote feed)

Async collectors — persistent coroutines, run as separate Docker services for now
----------------------------------------------------------------------------------
  shipping   — AIS WebSocket stream   (ShippingCollector, collectors/shipping.py)
  lightning  — OpenWeather REST        (LightningCollector, collectors/lightning.py)

  These two run as their own Docker services because the synchronous GFS/RTOFS
  downloads in DataCollector.collect_once() would starve their event loops. The
  consolidation path is to make collect_once() async (asyncio.to_thread + thread-safe
  DB handles), then spawn them as asyncio tasks inside DataCollector.run().
"""

import time
import logging

from .quakes import QuakeCollector
from .storms import StormsCollector
from .volcanoes import VolcanoesCollector
from .satellites import SatellitesCollector
from .markers_sync import MarkersSyncCollector

logger = logging.getLogger(__name__)

# Synchronous periodic collectors: driven by collect_event_feeds().
COLLECTORS = (QuakeCollector, StormsCollector, VolcanoesCollector, SatellitesCollector, MarkersSyncCollector)


def collect_event_feeds(config, db, last_runs: dict) -> None:
    """Run each event-feed collector, subject to per-collector scheduling.

    Collection is UNCONDITIONAL — it does NOT depend on the layer's `enabled` flag. The
    `enabled` flag is a FRONTEND visibility control (show/hide the layer); the data must
    already be in the DB so a layer renders instantly the moment a user enables it. So a
    collector runs whenever it is due, regardless of whether its layer is currently shown.

    Per-collector behaviour
    -----------------------
    * is_stale()      — gates on the collector's own runs_per_day (or update_hours for
                        satellites) so quakes (24/day) runs hourly while volcanoes (1/day)
                        runs once a day, regardless of the calling loop's cadence.
    * has_new_data()  — cheap HEAD/ETag check; if the remote is unchanged we record
                        the timestamp and move on without a full download.
    * collect()       — full fetch + DB upsert, called only when stale AND changed.

    last_runs is mutated in-place: {section -> time.monotonic() of last check}.
    The timestamp is updated on BOTH "collected" and "unchanged" outcomes so each
    collector's period counts down correctly between checks.
    """
    now = time.monotonic()
    for CollectorCls in COLLECTORS:
        key = CollectorCls.section
        try:
            feed = CollectorCls(config, db)
            if not feed.is_stale(last_runs.get(key)):
                logger.debug(
                    f"{key}: not yet due "
                    f"(period {feed.period_s:.0f}s, "
                    f"next in {feed.period_s - (time.monotonic() - (last_runs.get(key) or 0)):.0f}s)."
                )
                continue
            if not feed.has_new_data():
                last_runs[key] = now
                continue
            logger.info(f"{key}: collecting...")
            feed.collect()
            last_runs[key] = now
        except Exception as exc:
            logger.error(f"event feed {CollectorCls.__name__} failed: {exc}", exc_info=True)