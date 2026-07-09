#!/usr/bin/env python3
import sys
import os
import uuid
from datetime import datetime, timedelta, timezone

# Ensure we can import the atmos_gl module
sys.path.append(os.getcwd())
from atmos_gl.db.quake_adapter import QuakeAdapter


def generate_test_quakes():
    print("🌋 Generating test earthquakes...")
    quake_adapter = QuakeAdapter()
    now = datetime.now(timezone.utc)

    # Define test cases relative to current time
    # recent_hours default is 3, expiry_hours default is 12
    test_cases = [
        {"name": "Recent (1 hr)", "hours_ago": 1, "mag": 5.4, "depth": 12.0, "lat": -41.2, "lon": 174.7,
         "place": "15km W of Wellington, NZ"},
        {"name": "Recent (2 hrs)", "hours_ago": 2, "mag": 4.1, "depth": 5.5, "lat": -43.5, "lon": 172.6,
         "place": "10km S of Christchurch, NZ"},
        {"name": "Older (6 hrs)", "hours_ago": 6, "mag": 6.2, "depth": 33.0, "lat": -42.4, "lon": 173.6,
         "place": "20km E of Kaikoura, NZ"},
        {"name": "Older (10 hrs)", "hours_ago": 10, "mag": 3.8, "depth": 8.0, "lat": -38.1, "lon": 176.2,
         "place": "5km N of Rotorua, NZ"},
        {"name": "Expired (14 hrs) - Should not render", "hours_ago": 14, "mag": 4.5, "depth": 15.0, "lat": -39.0,
         "lon": 174.0, "place": "Offshore Taranaki, NZ"},
    ]

    count = 0
    for quake in test_cases:
        quake_id = f"usgs-test-{uuid.uuid4().hex[:8]}"
        timestamp = now - timedelta(hours=quake["hours_ago"])

        quake_adapter.update_quake(
            quake_id=quake_id,
            mag=quake["mag"],
            depth=quake["depth"],
            place=quake["place"],
            time_iso=timestamp.isoformat(),
            lat=quake["lat"],
            lon=quake["lon"]
        )
        print(f"Inserted: {quake['name']} - {quake['hours_ago']} hrs ago (Mag: {quake['mag']})")
        count += 1

    print(f"\nDone. Successfully injected {count} test earthquakes.")


if __name__ == "__main__":
    generate_test_quakes()