#!/usr/bin/env python3
"""Event-feed collectors: pure data sources that write straight to the DB.

Each collector is a `CollectorBase` subclass responsible for ONE external source. The
data_collector drives them via `collect_event_feeds()`, which handles per-collector rate
limiting (runs_per_day) and cheap remote-freshness checks (HEAD/ETag) independently.

Current event feeds
-------------------
  quakes     — USGS earthquake CSV, runs_per_day=24 (every ~hour)
  storms     — NHC/JTWC ATCF b/a-deck files, runs_per_day=8
  volcanoes  — NOAA HazEL REST API, runs_per_day=1

Eventual additions (async; will move here from src/worldmap/ when consolidated)
------------------
  shipping   — AIS WebSocket stream   (ShippingCollector)
  lightning  — OpenWeather REST        (LightningCollector)
  satellites — CelesTrak REST          (SatellitesCollector)

  These three are long-running async processes. The consolidation path is:
    1. Move their code to collectors/shipping.py, lightning.py, satellites.py
    2. Give each a CollectorBase mix-in for runs_per_day / is_stale()
    3. Spawn them as asyncio tasks inside DataCollector.run() via asyncio.gather(),
       so all data collection runs under a single service entry point.
"""

import time
import logging

from .quakes import QuakeCollector
from .storms import StormsCollector
from .volcanoes import VolcanoesCollector

logger = logging.getLogger(__name__)

# Registry order sets the run order within a single collect_event_feeds() call.
COLLECTORS = (QuakeCollector, StormsCollector, VolcanoesCollector)


def collect_event_feeds(config, db, last_runs: dict) -> None:
    """Run each enabled event-feed collector, subject to per-collector scheduling.

    Per-collector behaviour
    -----------------------
    * is_stale()      — gates on the collector's own runs_per_day so quakes (24/day)
                        runs hourly while volcanoes (1/day) runs once per day,
                        regardless of the calling loop's cadence.
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
            if not feed.enabled:
                logger.debug(f"{key}: disabled; skipping.")
                continue
            if not feed.is_stale(last_runs.get(key)):
                logger.debug(
                    f"{key}: not yet due "
                    f"(period {feed.period_s:.0f}s, "
                    f"next in {feed.period_s - (time.monotonic() - (last_runs.get(key) or 0)):.0f}s)."
                )
                continue
            if not feed.has_new_data():
                # Remote unchanged: record the check time so we don't re-hit HEAD
                # until the next period, but don't bother with a full download.
                last_runs[key] = now
                continue
            logger.info(f"{key}: collecting...")
            feed.collect()
            last_runs[key] = now
        except Exception as exc:
            logger.error(f"event feed {CollectorCls.__name__} failed: {exc}", exc_info=True)
