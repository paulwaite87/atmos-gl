#!/usr/bin/env python3
"""GDACS (Global Disaster Alert and Coordination System) event feed -> database.

Pure data (no render): pages through the GDACS event-list API, keeps events passing the
configured alert-level filters, and upserts them. The frontend reads them via the
/api/gdacs routes -- the browser never calls GDACS itself.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from terrascope.collectors.base import CollectorBase
from terrascope.db.gdacs_adapter import GdacsAdapter

logger = logging.getLogger(__name__)


ALERT_RANK = {"Green": 0, "Orange": 1, "Red": 2}
EVENT_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
# GDACS returns at most 100 events per page and ignores a pagesize parameter.
PAGE_SIZE = 100
# Footprint files run to hundreds of KB, so a cold start spreads them over several cycles.
DEFAULT_MAX_FOOTPRINTS_PER_CYCLE = 25
# The list request covers events overlapping the last N days -- enough to catch ones that
# ended within the housekeeper's grace period, while long-running floods/droughts still
# overlap the window for as long as GDACS keeps them current.
DEFAULT_LOOKBACK_DAYS = 14

# Popup text is trimmed to about this many characters (multi-country droughts run to 300+).
MAX_TEXT_CHARS = 80


def shorten_list_text(text: str | None, limit: int = MAX_TEXT_CHARS) -> str | None:
    """Collapse GDACS's doubled spaces and trailing comma; if the text is still longer
    than `limit`, cut at the last comma that fits and append "(+N more)" for the items
    dropped."""
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip().rstrip(",").strip()
    if len(text) <= limit:
        return text
    items = text.split(", ")
    kept = items[0]
    used = 1
    while used < len(items) and len(kept) + 2 + len(items[used]) <= limit:
        kept += ", " + items[used]
        used += 1
    return f"{kept} (+{len(items) - used} more)"


class GdacsCollector(CollectorBase):
    section = "gdacs"
    channel_key = "gdacs"
    datasource_key = "gdacs"

    def __init__(self, config):
        super().__init__(config)
        self.gdacs_adapter = GdacsAdapter()

    def _enabled_types(self) -> list[str]:
        configured = self.settings.get("types") or EVENT_TYPES
        return [t for t in EVENT_TYPES if t in configured]

    def _minimum_alert(self, event_type: str) -> str:
        overrides = self.settings.get("alert_overrides") or {}
        return overrides.get(event_type, self.settings.get("min_alert", "Orange"))

    def _request_params(self, page: int) -> dict:
        """Query parameters for one page of the event list. GDACS takes a single alert
        list for all types, so it carries every level at or above the lowest minimum of
        any enabled type; _passes_alert_filter() then applies the exact per-type rule.
        """
        lowest = min(ALERT_RANK[self._minimum_alert(t)] for t in self._enabled_types())
        levels = [a for a, rank in ALERT_RANK.items() if rank >= lowest]
        today = datetime.now(timezone.utc).date()
        lookback = int(self.settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        return {
            "eventlist": ";".join(self._enabled_types()),
            "alertlevel": ";".join(levels),
            "fromdate": (today - timedelta(days=lookback)).isoformat(),
            "todate": today.isoformat(),
            "pagenumber": page,
        }

    def _passes_alert_filter(self, event_type: str, alert_level: str) -> bool:
        """An event passes when its type is enabled and its alert level reaches the
        minimum for its type: the per-type override if one is configured, else the
        global min_alert."""
        if event_type not in self._enabled_types():
            return False
        return ALERT_RANK[alert_level] >= ALERT_RANK[self._minimum_alert(event_type)]

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
            "name": shorten_list_text(p.get("name")),
            "alert_level": p["alertlevel"],
            "alert_score": p.get("alertscore"),
            "severity": severity.get("severity"),
            "severity_text": severity.get("severitytext"),
            "severity_unit": severity.get("severityunit"),
            "country": shorten_list_text(p.get("country")),
            "iso3": p.get("iso3"),
            "from_date": p.get("fromdate"),
            "to_date": p.get("todate"),
            "date_modified": p.get("datemodified"),
            "is_current": str(p.get("iscurrent")).lower() == "true",
            "report_url": (p.get("url") or {}).get("report"),
            "lat": lat,
            "lon": lon,
        }

    def _fetch_footprint(self, feature: dict) -> bool:
        """Download the event's impact geometry and store it, returning whether a
        download was attempted. The centroid point is skipped -- it just repeats the
        event's own marker."""
        p = feature["properties"]
        if self.gdacs_adapter.footprint_marker(p["eventtype"], p["eventid"]) == p.get(
            "datemodified"
        ):
            return False  # stored geometry is already for this version of the event
        r = self._get((p.get("url") or {})["geometry"], timeout=60)
        if r is None:
            logger.error(
                f"GDACS: footprint fetch failed for {p['eventtype']}/{p['eventid']}."
            )
            return True
        parts = [
            {"feature_class": f["properties"]["Class"], "geometry": f["geometry"]}
            for f in r.json()["features"]
            if f.get("geometry") and f["properties"].get("Class") != "Point_Centroid"
        ]
        self.gdacs_adapter.replace_footprint(
            p["eventtype"], p["eventid"], p.get("datemodified"), parts
        )
        return True

    def collect(self) -> None:
        """Fetch the GDACS event list and upsert the events into the database."""
        url = self.datasource_url("gdacs")
        if not url:
            logger.warning("GDACS: no URL configured; skipping.")
            return

        count = 0
        footprint_downloads = 0
        max_footprints = int(
            self.settings.get(
                "max_footprints_per_cycle", DEFAULT_MAX_FOOTPRINTS_PER_CYCLE
            )
        )
        page = 1
        while True:
            r = self._get(url, timeout=30, params=self._request_params(page))
            if r is None:
                logger.error(f"GDACS: fetch of page {page} failed for {url!r}.")
                break
            features = r.json()["features"]
            for feature in features:
                p = feature["properties"]
                if not self._passes_alert_filter(p["eventtype"], p["alertlevel"]):
                    continue
                event = self._event_from_feature(feature)
                self.gdacs_adapter.upsert_event(event)
                count += 1
                if event["is_current"] and footprint_downloads < max_footprints:
                    footprint_downloads += self._fetch_footprint(feature)
            if len(features) < PAGE_SIZE:
                break
            page += 1

        logger.info(f"GDACS: upserted {count} events.")
