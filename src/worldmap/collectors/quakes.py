#!/usr/bin/env python3
"""USGS earthquake feed -> database.

Pure data (no render): fetches the USGS summary CSV, filters by minimum magnitude, and
upserts rows into the DB. The frontend reads them via the /api/quakes route.

HEAD check: the USGS CSV is a static file served via standard HTTP, so it carries ETag
and Last-Modified headers. We cache the last-seen marker and skip the (1 MB+) download
when the file hasn't changed since the previous run.
"""
import io
import logging

import requests
import pandas as pd

from worldmap.collectors.base import CollectorBase
from worldmap.db.quake_adapter import QuakeAdapter

logger = logging.getLogger(__name__)


class QuakeCollector(CollectorBase):
    section = "quakes"

    def __init__(self, config):
        super().__init__(config)
        self.quake_adapter = QuakeAdapter()

    def has_new_data(self) -> bool:
        url = self.settings.get("url", "")
        if not url:
            return True
        return self._head_changed_or_default(url, "Quakes")

    def collect(self) -> None:
        """Fetch USGS quake CSV and upsert into the database."""
        url = self.settings.get("url", "")
        min_mag = float(self.settings.get("min_mag", 3.5))
        if not url:
            logger.warning("Quakes: no URL configured; skipping.")
            return

        try:
            r = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "WorldMap-Collector/1.0"},
            )
            r.raise_for_status()

            df = pd.read_csv(io.StringIO(r.text))
            df["time"] = pd.to_datetime(df["time"])
            filtered = df[df["mag"] >= min_mag]

            count = 0
            for _, row in filtered.iterrows():
                self.quake_adapter.update_quake(
                    str(row["id"]),
                    float(row["mag"]),
                    float(row["depth"]),
                    str(row.get("place", "Unknown Location")),
                    row["time"].isoformat(),
                    float(row["latitude"]),
                    float(row["longitude"]),
                )
                count += 1

            logger.info(f"Quakes: upserted {count} records (min_mag={min_mag}).")
        except requests.RequestException as e:
            logger.error(f"Quakes: fetch failed: {e}")
