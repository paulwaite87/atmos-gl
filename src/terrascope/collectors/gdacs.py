#!/usr/bin/env python3
"""GDACS (Global Disaster Alert and Coordination System) event feed -> database.

Pure data (no render): pages through the GDACS event-list API, keeps events passing the
configured alert-level filters, and upserts them. The frontend reads them via the
/api/gdacs routes -- the browser never calls GDACS itself.
"""

import logging

from terrascope.collectors.base import CollectorBase
from terrascope.db.gdacs_adapter import GdacsAdapter

logger = logging.getLogger(__name__)


ALERT_RANK = {"Green": 0, "Orange": 1, "Red": 2}
EVENT_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
# GDACS returns at most 100 events per page and ignores a pagesize parameter.
PAGE_SIZE = 100


class GdacsCollector(CollectorBase):
    section = "gdacs"
    channel_key = "gdacs"
    datasource_key = "gdacs"

    def __init__(self, config):
        super().__init__(config)
        self.gdacs_adapter = GdacsAdapter()

    def _passes_alert_filter(self, event_type: str, alert_level: str) -> bool:
        """An event passes when its type is enabled and its alert level reaches the
        minimum for its type: the per-type override if one is configured, else the
        global min_alert."""
        if event_type not in (self.settings.get("types") or EVENT_TYPES):
            return False
        overrides = self.settings.get("alert_overrides") or {}
        minimum = overrides.get(event_type, self.settings.get("min_alert", "Orange"))
        return ALERT_RANK[alert_level] >= ALERT_RANK[minimum]

    @staticmethod
    def _event_from_feature(feature: dict) -> dict:
        """One GDACS GeoJSON feature -> the adapter's event dict. GDACS sends booleans
        as the strings "true"/"false" and dates as naive UTC ISO strings."""
        p = feature["properties"]
        severity = p.get("severitydata") or {}
        lon, lat = feature["geometry"]["coordinates"]
        return {
            "event_type": p["eventtype"],
            "event_id": p["eventid"],
            "episode_id": p.get("episodeid"),
            "name": p.get("name"),
            "alert_level": p["alertlevel"],
            "alert_score": p.get("alertscore"),
            "severity": severity.get("severity"),
            "severity_text": severity.get("severitytext"),
            "severity_unit": severity.get("severityunit"),
            "country": p.get("country"),
            "iso3": p.get("iso3"),
            "from_date": p.get("fromdate"),
            "to_date": p.get("todate"),
            "date_modified": p.get("datemodified"),
            "is_current": str(p.get("iscurrent")).lower() == "true",
            "report_url": (p.get("url") or {}).get("report"),
            "lat": lat,
            "lon": lon,
        }

    def collect(self) -> None:
        """Fetch the GDACS event list and upsert the events into the database."""
        url = self.datasource_url("gdacs")
        if not url:
            logger.warning("GDACS: no URL configured; skipping.")
            return

        count = 0
        page = 1
        while True:
            r = self._get(url, timeout=30, params={"pagenumber": page})
            if r is None:
                logger.error(f"GDACS: fetch of page {page} failed for {url!r}.")
                break
            features = r.json()["features"]
            for feature in features:
                p = feature["properties"]
                if not self._passes_alert_filter(p["eventtype"], p["alertlevel"]):
                    continue
                self.gdacs_adapter.upsert_event(self._event_from_feature(feature))
                count += 1
            if len(features) < PAGE_SIZE:
                break
            page += 1

        logger.info(f"GDACS: upserted {count} events.")
