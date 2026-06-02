#!/usr/bin/env python3
import logging
from datetime import datetime, timezone
from worldmap.lib.db import Database
from .common import Updater

logger = logging.getLogger(__name__)

ACTIVE_STRIKE_MINS = 15
OLDER_STRIKE_MINS = 120


class LightningUpdater(Updater):
    def __init__(self, config, map_data):
        super().__init__(config, "Lightning", map_data)
        self.set_output_path()
        self.strike_recent_minutes = self.settings.get("strike_recent_minutes", 15)
        self.strike_keep_minutes = self.settings.get("strike_keep_minutes", 60)
        self.strike_expiry_minutes = (self.settings.get("strike_expiry_hours", 1) * 60)

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

        written_count = 0
        now = datetime.now(timezone.utc)

        with open(self.output_path, "w") as f:
            for s in strikes:
                # Calculate age for icon logic
                age_mins = (now - s["timestamp"]).total_seconds() / 60

                if age_mins <= self.strike_recent_minutes:
                    icon = "bolt_white.png"
                elif age_mins <= self.strike_keep_minutes:
                    icon = "bolt_yellow.png"
                else:
                    icon = "bolt_red.png"

                f.write(f"{s['lat']} {s['lon']} image={icon}\n")
                written_count += 1

        logger.info(f"Placed {written_count} strikes")
