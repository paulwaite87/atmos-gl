#!/usr/bin/env python3
"""External data collectors that resolve to DATABASE rows (no render, no fieldstore).

These were previously layer_builder updaters that fetched-and-wrote on the render
cadence. They're pure data, so the data_collector now owns the fetch and the frontend
reads the results via /api/ routes.
"""
import logging

from .quakes import QuakeCollector
from .storms import StormsCollector
from .volcanoes import VolcanoesCollector

logger = logging.getLogger(__name__)

COLLECTORS = (QuakeCollector, StormsCollector, VolcanoesCollector)


def collect_event_feeds(config, db):
    """Run each enabled event feed once, isolating failures so one bad feed can't stop
    the others."""
    for CollectorCls in COLLECTORS:
        try:
            feed = CollectorCls(config, db)
            if not feed.enabled:
                logger.debug(f"{CollectorCls.__name__}: layer disabled; skipping.")
                continue
            feed.collect()
        except Exception as e:
            logger.error(f"event feed {CollectorCls.__name__} failed: {e}")