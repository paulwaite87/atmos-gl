#!/usr/bin/env python3
import sys
import os

# Add the current directory to sys.path so we can import the worldmap module
sys.path.append(os.getcwd())

from worldmap.lib.db import Database
from worldmap.lib.shipping import get_vessel_class_from_type


def main():
    print("Starting vessel_class population script...")

    try:
        db = Database()
        # Using a cursor directly to iterate over existing records
        with db.conn.cursor() as cur:
            # Fetch all ships that have a vessel_type
            cur.execute("SELECT mmsi, vessel_type FROM ships WHERE vessel_type IS NOT NULL")
            ships = cur.fetchall()

            print(f"Found {len(ships)} ships to update.")

            count = 0
            for ship in ships:
                mmsi = ship['mmsi']
                v_type = ship['vessel_type']

                # Use your existing logic to get the class
                new_class = get_vessel_class_from_type(v_type)

                # Perform the update
                update_sql = "UPDATE ships SET vessel_class = %s WHERE mmsi = %s"
                cur.execute(update_sql, (new_class, mmsi))

                count += 1
                if count % 100 == 0:
                    print(f"Processed {count} ships...")

        print(f"Successfully updated {count} records. Migration complete.")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()