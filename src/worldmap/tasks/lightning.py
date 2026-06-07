#!/usr/bin/env python3
import logging
from datetime import datetime, timezone
from worldmap.lib.db import Database
from .common import Updater

logger = logging.getLogger(__name__)


class LightningUpdater(Updater):
    def __init__(self, config, map_data):
        super().__init__(config, "Lightning", map_data)
        self.strike_recent_minutes = self.settings.get("strike_recent_minutes", 15)
        self.strike_keep_minutes = self.settings.get("strike_keep_minutes", 60)
        self.strike_expiry_minutes = self.settings.get("strike_expiry_hours", 1) * 60

    async def run(self):
        self.exit_if_disabled()

        db = Database()
        # Use the bbox from your common Updater class
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox

        # Fetch from DB (much faster than API tiling). Will only
        # return non-expired lightning strikes.
        strikes = db.get_lightning_in_region(
            lon_min,
            lat_min,
            lon_max,
            lat_max,
            expiry_minutes=self.strike_expiry_minutes,
        )

        now = datetime.now(timezone.utc)

        strikes_list = []

        for s in strikes:
            # Calculate age for icon logic
            strike_time = s["timestamp"]
            age_minutes = (now - strike_time).total_seconds() / 60
            age_hours = int(age_minutes / 60)
            is_recent = age_hours <= self.strike_recent_minutes

            strike_data = {
                "lat": s["lat"],
                "lng": s["lon"],
                "label": f"Strike at {strike_time.strftime('%H:%M')}",
                "age_hours": age_hours,
                "age_minutes": age_minutes,  # <-- Added for fine-grained frontend display
                "is_recent": is_recent,
            }

            strikes_list.append(strike_data)

        logger.info(f"Captured {len(strikes_list)} lightning strikes")
