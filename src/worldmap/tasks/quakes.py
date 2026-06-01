#!/usr/bin/env python3
import io
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
        """Fetches USGS quake data and generates an XPlanet marker file."""
        self.exit_if_disabled()

        url = self.get_base_url()
        marker_color = self.settings.get("marker_color", fallback="white")
        marker_symbol_new = self.settings.get("marker_symbol_new")
        marker_symbol_old = self.settings.get("marker_symbol_old")
        label_size = self.settings.get("label_fontsize", fallback="12")
        min_mag = self.settings.getfloat("min_mag", fallback=5.0)
        recent_activity_hours = self.settings.getint(
            "recent_activity_hours", fallback=3
        )
        expiry_hours = self.settings.getint("expiry_hours", fallback=24)

        try:
            logger.debug(f"Fetching earthquake data from USGS (Min Mag: {min_mag})...")
            r = requests.get(url, timeout=15)
            r.raise_for_status()

            # Load CSV data into Pandas
            df = pd.read_csv(io.StringIO(r.text))

            # Parse the time column into timezone-aware datetimes ---
            df["time"] = pd.to_datetime(df["time"])

            # Filter by magnitude
            filtered_df = df[df["mag"] >= min_mag]

            # Establish 'now' in UTC to compare against the USGS 'Z' (Zulu/UTC) timestamps
            now_utc = datetime.now(timezone.utc)

            with open(self.output_path, "w") as f:
                for _, row in filtered_df.iterrows():
                    mag = row["mag"]
                    depth = int(row["depth"])
                    quake_time = row["time"]

                    # Calculate age and assign the correct symbol
                    age_hours = int((now_utc - quake_time).total_seconds() / 3600.0)

                    if age_hours >= expiry_hours:
                        continue
                    elif age_hours <= recent_activity_hours:
                        marker_symbol = marker_symbol_new
                    else:
                        marker_symbol = marker_symbol_old

                    # Format: lat lon "label" color=X fontsize=Y image=Z
                    line = (
                        f"{row['latitude']} {row['longitude']} "
                        f'"M{mag} {depth}km" color={marker_color} '
                        f"fontsize={label_size} image={marker_symbol}\n"
                    )
                    f.write(line)

            logger.debug(
                f"Earthquake update complete. Updated {len(filtered_df)} quakes."
            )

        except requests.RequestException as e:
            logger.error(f"Error fetching quakes: {e}")
