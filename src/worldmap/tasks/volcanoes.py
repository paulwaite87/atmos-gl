#!/usr/bin/env python3
import json
import logging
import urllib.error
import urllib.request

# Internal library import
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class VolcanoUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Volcanoes", map_data)

    def _fetch_volcano_data(self, base_url, page_size=200):
        """Fetch all records from the NOAA HazEL API with pagination."""
        items = []
        page = 1
        try:
            while True:
                url = f"{base_url}?page={page}&itemsPerPage={page_size}"
                req = urllib.request.Request(
                    url, headers={"Accept": "application/json"}
                )

                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    batch = data.get("items", [])
                    if not batch:
                        break
                    items.extend(batch)

                    # Stop if we've reached the total count reported by API
                    if len(items) >= data.get("count", 0):
                        break
                    page += 1
            return items
        except Exception as e:
            logger.error(f"Error connecting to NOAA HazEL API: {e}")
            return []

    def run(self):
        db = Database()
        records = self._fetch_volcano_data(self.get_base_url())

        for r in records:
            # We use an ID based on name or provided field if available
            v_id = r.get("id", r.get("name"))
            db.update_volcano(
                v_id,
                r.get("name"),
                r.get("latitude"),
                r.get("longitude"),
                r.get("vei", 0),
                r.get("significant", False),
                r.get("timeErupt", ""),
            )
