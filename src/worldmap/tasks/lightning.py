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
        self.age_minutes = self.settings.getint("expiry_hours", fallback=1) * 60

    async def run(self):
        self.exit_if_disabled()

        db = Database()
        # Use the bbox from your common Updater class
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox

        # Fetch from DB (much faster than API tiling)
        strikes = db.get_lightning_in_region(
            lon_min, lat_min, lon_max, lat_max, age_minutes=self.age_minutes
        )

        written_count = 0
        now = datetime.now(timezone.utc)

        with open(self.output_path, "w") as f:
            for s in strikes:
                # Calculate age for icon logic
                age_mins = (now - s["timestamp"]).total_seconds() / 60

                if age_mins < ACTIVE_STRIKE_MINS:
                    icon = "bolt_white.png"
                elif age_mins < OLDER_STRIKE_MINS:
                    icon = "bolt_yellow.png"
                else:
                    icon = "bolt_red.png"

                f.write(f"{s['lat']} {s['lon']} image={icon}\n")
                written_count += 1

        logger.info(f"Placed {written_count} strikes")
