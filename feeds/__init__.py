#!/usr/bin/env python3
"""External event feeds that resolve to DATABASE rows (no render, no fieldstore).

These were previously layer_builder updaters that fetched-and-wrote on the render
cadence. They're pure data, so the data_collector now owns the fetch and the frontend
reads the results via the /api/{quakes,storms,volcanoes} routes.
"""
import logging

from worldmap.feeds.quakes import QuakeFeed
from worldmap.feeds.storms import StormFeed
from worldmap.feeds.volcanoes import VolcanoFeed

logger = logging.getLogger(__name__)

FEEDS = (QuakeFeed, StormFeed, VolcanoFeed)


def collect_event_feeds(config, db):
    """Run each enabled event feed once, isolating failures so one bad feed can't stop
    the others."""
    for FeedCls in FEEDS:
        try:
            feed = FeedCls(config, db)
            if not feed.enabled:
                logger.debug(f"{FeedCls.__name__}: layer disabled; skipping.")
                continue
            feed.collect()
        except Exception as e:
            logger.error(f"event feed {FeedCls.__name__} failed: {e}")