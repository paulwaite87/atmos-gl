#!/usr/bin/env python3
"""IODA (Georgia Tech Internet Outage Detection and Analysis) country outage events ->
database.

Pure data (no render): each cycle asks IODA for every country-level event overlapping the
lookback window and upserts it. The frontend reads it via /api/internet_outages -- the
browser never calls IODA.
"""

import logging
import time
from datetime import datetime, timezone

from terrascope.collectors.base import CollectorBase
from terrascope.db.internet_outages_adapter import InternetOutagesAdapter

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 3
# Shorter outages are mostly probing blips, not something worth shading a country for.
DEFAULT_MIN_DURATION_MINUTES = 30
# IODA clips an ongoing event's duration at the moment of the request, so an event whose
# end is within this many seconds of "until" is still going.
ONGOING_TOLERANCE_S = 600
# A country-level query returns well under a hundred events; this is generous headroom.
EVENT_LIMIT = 1000


class InternetOutagesCollector(CollectorBase):
    section = "internet_outages"
    channel_key = "internet_outages"
    datasource_key = "internet_outages"

    def __init__(self, config):
        super().__init__(config)
        self.internet_outages_adapter = InternetOutagesAdapter()

    @staticmethod
    def _event_from_ioda(raw: dict, until: int) -> dict:
        """One IODA event -> the adapter's event dict."""
        start = datetime.fromtimestamp(raw["start"], timezone.utc)
        end_ts = raw["start"] + raw["duration"]
        return {
            "country_code": raw["location"].split("/", 1)[1],
            "country_name": raw["location_name"],
            "signal": raw["datasource"],
            "start": start,
            "end": datetime.fromtimestamp(end_ts, timezone.utc),
            "ongoing": end_ts >= until - ONGOING_TOLERANCE_S,
            "score": raw["score"],
        }

    def collect(self) -> None:
        """Fetch the country-level events overlapping the lookback window and upsert them."""
        url = self.datasource_url("internet_outages")
        if not url:
            logger.warning("Internet outages: no URL configured; skipping.")
            return
        until = int(time.time())
        lookback_s = (
            int(self.settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS)) * 86400
        )
        r = self._get(
            url,
            timeout=60,
            params={
                "entityType": "country",
                "from": until - lookback_s,
                "until": until,
                "limit": EVENT_LIMIT,
            },
        )
        if r is None:
            logger.error(f"Internet outages: fetch failed for {url!r}.")
            return
        try:
            raw_events = r.json()["data"]
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Internet outages: unreadable response from {url!r}: {e!r}")
            return
        min_duration_s = 60 * float(
            self.settings.get("min_duration_minutes", DEFAULT_MIN_DURATION_MINUTES)
        )
        seen = set()
        count = 0
        for raw in raw_events:
            try:
                event = self._event_from_ioda(raw, until)
            except (KeyError, TypeError, ValueError, IndexError) as e:
                logger.warning(
                    f"Internet outages: skipping malformed event {raw!r}: {e!r}"
                )
                continue
            if (event["end"] - event["start"]).total_seconds() < min_duration_s:
                continue
            self.internet_outages_adapter.upsert_event(event)
            seen.add((event["country_code"], event["signal"], event["start"]))
            count += 1
        # IODA drops resolved events from its answers; close any we still hold as ongoing.
        self.internet_outages_adapter.close_unseen_ongoing(seen)
        logger.info(f"Internet outages: upserted {count} events.")
