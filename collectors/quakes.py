#!/usr/bin/env python3
"""USGS earthquake feed -> database.

Moved out of the layer_builder task tree: quakes are pure data (no render), so the
data_collector fetches the USGS feed and upserts rows; the frontend reads them via the
/api/quakes route. No PNG, no fieldstore — straight to the DB.
"""
import io
import logging

import requests
import pandas as pd

logger = logging.getLogger(__name__)

SECTION = "quakes"


class QuakeCollector:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.settings = config.get_section(SECTION) or {}

    @property
    def enabled(self):
        return bool(self.settings.get("enabled", False))

    def base_url(self):
        return self.settings.get("url", "").rstrip("/")

    def collect(self):
        """Fetch USGS quake data and upsert it into the database."""
        url = self.base_url()
        min_mag = self.settings.get("min_mag", 3.5)

        try:
            logger.debug(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            df = pd.read_csv(io.StringIO(r.text))
            df["time"] = pd.to_datetime(df["time"])
            filtered_df = df[df["mag"] >= min_mag]

            count = 0
            for _, row in filtered_df.iterrows():
                quake_id = str(row["id"])
                mag = float(row["mag"])
                depth = float(row["depth"])
                place = str(row.get("place", "Unknown Location"))
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                time_iso = row["time"].isoformat()

                self.db.update_quake(quake_id, mag, depth, place, time_iso, lat, lon)
                count += 1

            logger.debug(
                f"Earthquake update complete. UPSERTed {count} quakes to Database."
            )
        except requests.RequestException as e:
            logger.error(f"Error fetching quakes: {e}")
