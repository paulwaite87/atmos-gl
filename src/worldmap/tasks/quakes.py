#!/usr/bin/env python3
import io
import json
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class QuakeUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Quakes", map_data)
        self.set_output_path()

    def run(self):
        """Fetches USGS quake data and generates a JSON array for Globe.gl."""
        self.exit_if_disabled()

        url = self.get_base_url()
        min_mag = self.settings.get("min_mag", 3.5)
        recent_activity_hours = self.settings.get("recent_activity_hours", 3)
        expiry_hours = self.settings.get("expiry_hours", 12)

        try:
            logger.debug(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            # Load CSV data into Pandas
            df = pd.read_csv(io.StringIO(r.text))

            # Parse the time column into timezone-aware datetimes
            df["time"] = pd.to_datetime(df["time"])

            # Filter by magnitude
            filtered_df = df[df["mag"] >= min_mag]

            # Establish 'now' in UTC to compare against the USGS timestamps
            now_utc = datetime.now(timezone.utc)
            quakes_list = []

            for _, row in filtered_df.iterrows():
                mag = row["mag"]
                depth = int(row["depth"])
                quake_time = row["time"]

                # Calculate precise age metrics
                time_delta = now_utc - quake_time
                age_minutes = int(time_delta.total_seconds() / 60.0)
                age_hours = int(age_minutes / 60)

                # Skip if the quake has expired
                if age_hours >= expiry_hours:
                    continue

                # Flag recent activity
                is_recent = age_hours <= recent_activity_hours

                place = row.get("place", "Unknown Location")
                if pd.isna(place):
                    place = "Unknown Location"

                # Build the dictionary with fine-grained time data
                quake_data = {
                    "lat": row['latitude'],
                    "lng": row['longitude'],
                    "mag": mag,
                    "depth": depth,
                    "place": place,
                    "label": f"M {mag:.1f} - {place}",
                    "age_hours": age_hours,
                    "age_minutes": age_minutes,  # <-- Added for fine-grained frontend display
                    "is_recent": is_recent
                }

                quakes_list.append(quake_data)

            # Dump the entire list of dictionaries directly to a JSON file
            with open(self.output_path, "w") as f:
                json.dump(quakes_list, f, indent=2)

            logger.debug(
                f"Earthquake update complete. Wrote {len(quakes_list)} quakes to JSON."
            )

        except requests.RequestException as e:
            logger.error(f"Error fetching quakes: {e}")