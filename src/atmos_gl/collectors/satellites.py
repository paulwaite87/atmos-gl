#!/usr/bin/env python3
"""CelesTrak satellite orbital elements -> database.

Pure data (no render): fetches OMM records for configured groups and upserts them. The
frontend reads them via the /api/satellites route.

Converted from async (aiohttp) to synchronous (requests) so it can join the periodic
collect_event_feeds() loop inside DataCollector instead of running as a separate service.
With runs_per_day driving a several-times-a-day cadence there is no benefit to async I/O
here.

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
    channel_key = "satellites"
    datasource_key = "satellites"

    def __init__(self, config):
        super().__init__(config)
        self.satellite_adapter = SatelliteAdapter()

    def _groups(self) -> list[str]:
        raw = self.settings.get("groups", "stations,weather,science,resource")
        return [g.strip() for g in raw.split(",") if g.strip()]

    def source_url(self) -> str | None:
        """CelesTrak's default endpoint when data_collector.datasources.satellites
        isn't configured -- collect()/has_new_data() have always had a working
        hardcoded fallback; the Data Status link should show it too rather than going
        blank (see the base class's source_url())."""
        return super().source_url() or "https://celestrak.org/NORAD/elements"

    def has_new_data(self) -> bool:
        """HEAD the stations-group URL as a proxy for the whole dataset."""
        url = f"{self.source_url()}/gp.php?GROUP=stations&FORMAT=json"
        return self._head_changed_or_default(url, "Satellites")

    def _fetch_group(self, group: str) -> list:
        url = f"{self.source_url()}/gp.php?GROUP={group}&FORMAT=json"
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
