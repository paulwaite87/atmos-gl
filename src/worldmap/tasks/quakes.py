#!/usr/bin/env python3
import io
import logging
import requests
import pandas as pd
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.db import Database
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class QuakeUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Quakes", map_data)

    def run(self):
        """Fetches USGS quake data and stores it in the database."""
        self.exit_if_disabled()

        url = self.get_base_url()
        min_mag = self.settings.get("min_mag", 3.5)

        try:
            logger.debug(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            df = pd.read_csv(io.StringIO(r.text))
            df["time"] = pd.to_datetime(df["time"])
            filtered_df = df[df["mag"] >= min_mag]

            db = Database()
            count = 0

            for _, row in filtered_df.iterrows():
                # USGS CSV includes an 'id' column natively
                quake_id = str(row["id"])
                mag = float(row["mag"])
                depth = float(row["depth"])
                place = str(row.get("place", "Unknown Location"))
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                time_iso = row["time"].isoformat()

                db.update_quake(quake_id, mag, depth, place, time_iso, lat, lon)
                count += 1

            logger.debug(f"Earthquake update complete. UPSERTed {count} quakes to Database.")

        except requests.RequestException as e:
            logger.error(f"Error fetching quakes: {e}")