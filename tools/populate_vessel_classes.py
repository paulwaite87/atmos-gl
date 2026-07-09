#!/usr/bin/env python3
import sys
import os

# Add the current directory to sys.path so we can import the atmos_gl module
sys.path.append(os.getcwd())

from sqlalchemy import select

from atmos_gl.db.engine import Session
from atmos_gl.db.models import Ship
from atmos_gl.lib.shipping import get_vessel_class_from_type


def main():
    print("Starting vessel_class population script...")

    try:
        with Session() as session:
            ships = session.execute(
                select(Ship.mmsi, Ship.vessel_type).where(Ship.vessel_type.isnot(None))
            ).all()

            print(f"Found {len(ships)} ships to update.")

            count = 0
            for mmsi, v_type in ships:
                # Use your existing logic to get the class
                new_class = get_vessel_class_from_type(v_type)

                session.execute(
                    Ship.__table__.update()
                    .where(Ship.mmsi == mmsi)
                    .values(vessel_class=new_class)
                )

                count += 1
                if count % 100 == 0:
                    print(f"Processed {count} ships...")

            session.commit()

        print(f"Successfully updated {count} records. Migration complete.")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()