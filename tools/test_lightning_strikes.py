#!/usr/bin/env python3
import sys
import os
import uuid
from datetime import datetime, timedelta, timezone

sys.path.append(os.getcwd())
from atmos_gl.db.lightning_adapter import LightningAdapter


def generate_test_strikes():
    print("⚡ Generating test lightning strikes...")
    lightning_adapter = LightningAdapter()
    now = datetime.now(timezone.utc)

    # Define test cases relative to current time
    test_cases = [
        {"name": "White (Recent)", "minutes_ago": 5, "lat": -40.0, "lon": 175.0},
        {"name": "White (Recent)", "minutes_ago": 12, "lat": -41.0, "lon": 174.0},
        {"name": "Yellow (Keep)", "minutes_ago": 30, "lat": -39.0, "lon": 176.0},
        {"name": "Yellow (Keep)", "minutes_ago": 45, "lat": -38.0, "lon": 177.0},
        {"name": "Red (Expiring)", "minutes_ago": 90, "lat": -42.0, "lon": 173.0},
        {"name": "Red (Expiring)", "minutes_ago": 110, "lat": -43.0, "lon": 172.0},
    ]

    for strike in test_cases:
        strike_id = f"test-strike-{uuid.uuid4().hex[:8]}"
        timestamp = now - timedelta(minutes=strike["minutes_ago"])

        lightning_adapter.update_lightning_strike(
            strike_id=strike_id,
            lat=strike["lat"],
            lon=strike["lon"],
            quality="TEST",
            timestamp_iso=timestamp.isoformat()
        )
        print(f"Inserted: {strike['name']} - {strike['minutes_ago']} mins ago (ID: {strike_id})")


if __name__ == "__main__":
    generate_test_strikes()