#!/usr/bin/env python3
"""NOAA HazEL volcano feed -> database.

Pure data (no render): fetches the paginated HazEL REST API and upserts rows. The
frontend reads them via the /api/volcanoes route.

HEAD check: HazEL is a REST API and may return 405 for HEAD requests. We try it
opportunistically — if the server supplies ETag/Last-Modified headers we use them to
skip unchanged data; if not (or on any error) we fall through to collect() safely.
With runs_per_day=1 the saving is modest, but the pattern is consistent.
"""
import json
import logging
import urllib.request

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.db.volcano_adapter import VolcanoAdapter

logger = logging.getLogger(__name__)


class VolcanoesCollector(CollectorBase):
    section = "volcanoes"
    channel_key = "volcanoes"
    datasource_key = "volcanoes"

    def __init__(self, config):
        super().__init__(config)
        self.volcano_adapter = VolcanoAdapter()

    def has_new_data(self) -> bool:
        url = self.source_url()
        if not url:
            return True
        return self._head_changed_or_default(url, "Volcanoes")

    def _fetch_all(self, base_url, page_size=200):
        """Fetch all records from the NOAA HazEL API with pagination."""
        items = []
        page = 1
        try:
            while True:
                url = f"{base_url}?page={page}&itemsPerPage={page_size}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "AtmosGL-Collector/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    batch = data.get("items", [])
                    if not batch:
                        break
                    items.extend(batch)
                    if len(items) >= data.get("count", 0):
                        break
                    page += 1
        except Exception as e:
            logger.error(f"Volcanoes: fetch failed: {e}")
        return items

    def collect(self) -> None:
        records = self._fetch_all(self.source_url())
        count = 0
        for r in records:
            v_id = r.get("id", r.get("name"))
            self.volcano_adapter.update_volcano(
                v_id,
                r.get("name"),
                r.get("latitude"),
                r.get("longitude"),
                r.get("vei", 0),
                r.get("significant", False),
                r.get("timeErupt", ""),
            )
            count += 1
        logger.info(f"Volcanoes: upserted {count} records.")
