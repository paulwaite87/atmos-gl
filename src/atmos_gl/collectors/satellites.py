#!/usr/bin/env python3
"""CelesTrak satellite orbital elements -> database.

Pure data (no render): fetches OMM records for configured groups and upserts them. The
frontend reads them via the /api/satellites route.

Converted from async (aiohttp) to synchronous (requests) so it can join the periodic
collect_event_feeds() loop inside DataCollector instead of running as a separate service.
With update_hours=12 there is no benefit to async I/O here.

HEAD check: we probe the stations-group URL as a freshness proxy for the whole dataset
(CelesTrak updates all groups together). If Last-Modified/ETag is unchanged we skip the
full multi-group download.
"""
import logging

import requests

from .base import CollectorBase
from atmos_gl.db.satellite_adapter import SatelliteAdapter

logger = logging.getLogger(__name__)


class SatellitesCollector(CollectorBase):
    section = "satellites_collector"

    def __init__(self, config):
        super().__init__(config)
        self.satellite_adapter = SatelliteAdapter()

    @property
    def period_s(self) -> float:
        """Derive period from update_hours config key (keeps existing config compatible)."""
        hours = float(self.settings.get("update_hours", 12))
        return hours * 3600.0

    def _groups(self) -> list[str]:
        raw = self.settings.get("groups", "stations,weather,science,resource")
        return [g.strip() for g in raw.split(",") if g.strip()]

    def _base_url(self) -> str:
        return self.settings.get("url", "https://celestrak.org/NORAD/elements").rstrip("/")

    def has_new_data(self) -> bool:
        """HEAD the stations-group URL as a proxy for the whole dataset."""
        url = f"{self._base_url()}/gp.php?GROUP=stations&FORMAT=json"
        return self._head_changed_or_default(url, "Satellites")

    def _fetch_group(self, group: str) -> list:
        url = f"{self._base_url()}/gp.php?GROUP={group}&FORMAT=json"
        try:
            r = requests.get(
                url,
                timeout=30,
                headers={"User-Agent": "AtmosGL-Collector/1.0"},
            )
            if r.status_code == 200:
                # CelesTrak sometimes serves JSON as text/plain
                return r.json()
            logger.warning(f"Satellites: group '{group}' returned HTTP {r.status_code}")
        except Exception as exc:
            logger.warning(f"Satellites: failed to fetch group '{group}': {exc}")
        return []

    def collect(self) -> None:
        stored = 0
        for group in self._groups():
            for rec in self._fetch_group(group):
                try:
                    norad = int(rec["NORAD_CAT_ID"])
                    name = rec.get("OBJECT_NAME", str(norad))
                    # Store the full OMM dict verbatim — omm.initialize() needs it all.
                    self.satellite_adapter.update_satellite(norad, name, rec, rec.get("EPOCH"))
                    stored += 1
                except Exception as exc:
                    logger.debug(f"Satellites: skipping malformed record: {exc}")
        logger.info(f"Satellites: stored/updated {stored} objects.")
