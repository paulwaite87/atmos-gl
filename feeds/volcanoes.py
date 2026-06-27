#!/usr/bin/env python3
"""NOAA HazEL volcano feed -> database.

Pure data (no render): the data_collector fetches the paginated HazEL API and upserts
rows; the frontend reads them via the /api/volcanoes route.
"""
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

SECTION = "volcanoes"


class VolcanoFeed:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.settings = config.get_section(SECTION) or {}

    @property
    def enabled(self):
        return bool(self.settings.get("enabled", False))

    def base_url(self):
        return self.settings.get("url", "").rstrip("/")

    def _fetch_volcano_data(self, base_url, page_size=200):
        """Fetch all records from the NOAA HazEL API with pagination."""
        items = []
        page = 1
        try:
            while True:
                url = f"{base_url}?page={page}&itemsPerPage={page_size}"
                req = urllib.request.Request(url, headers={"Accept": "application/json"})

                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    batch = data.get("items", [])
                    if not batch:
                        break
                    items.extend(batch)

                    if len(items) >= data.get("count", 0):
                        break
                    page += 1
            return items
        except Exception as e:
            logger.error(f"Error connecting to NOAA HazEL API: {e}")
            return []

    def collect(self):
        records = self._fetch_volcano_data(self.base_url())

        for r in records:
            v_id = r.get("id", r.get("name"))
            self.db.update_volcano(
                v_id,
                r.get("name"),
                r.get("latitude"),
                r.get("longitude"),
                r.get("vei", 0),
                r.get("significant", False),
                r.get("timeErupt", ""),
            )
