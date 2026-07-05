#!/usr/bin/env python3
import logging

from sqlalchemy import func, select

# Import your core modules based on the project structure
from worldmap.db.engine import Session
from worldmap.db.models import Storm, StormTrack
from worldmap.lib.config import WorldMapConfig
from worldmap.tasks.common import MapData
from worldmap.tasks.storms import StormUpdater

# Configure basic logging so you can watch the progress in the terminal
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_wkt_polygon(vertices):
    """Converts a list of (lon, lat) tuples to PostGIS WKT Polygon format."""
    if not vertices or len(vertices) < 3:
        return None

    # Ensure the polygon is closed (last point == first point)
    if vertices[0] != vertices[-1]:
        vertices.append(vertices[0])

    coords = ", ".join([f"{lon} {lat}" for lon, lat in vertices])
    return f"POLYGON(({coords}))"


def refresh_all_cones():
    logger.info("Initializing dependencies...")

    # 1. Core Initialization
    config = WorldMapConfig(config_path="/opt/project/config/worldmap.json")
    updater = StormUpdater(config, MapData(config))

    try:
        # 2. Get all Storm IDs
        with Session() as session:
            storms = session.execute(select(Storm.sid)).all()

        if not storms:
            logger.info("No active storms found in the database.")
            return

        logger.info(f"Found {len(storms)} storms. Rebuilding cones...")

        for (sid,) in storms:
            # 3. Fetch forecast points AND the current point for this storm
            with Session() as session:
                track_data = session.execute(
                    select(StormTrack.lat, StormTrack.lon, StormTrack.tau)
                    .where(
                        StormTrack.sid == sid,
                        StormTrack.record_type.in_(["CURRENT", "FORECAST"]),
                    )
                    .order_by(StormTrack.tau)
                ).all()

            if len(track_data) < 2:
                logger.debug(f"Skipping {sid}: Not enough forecast points to build a cone.")
                continue

            # 4. Format for the StormUpdater calculator
            forecast_points = [
                {"LAT": float(lat), "LON": float(lon), "TAU": int(tau or 0)}
                for lat, lon, tau in track_data
            ]

            # 5. Calculate Geometry using your official class logic
            vertices = updater._build_cone_polygons(forecast_points)

            if vertices:
                wkt = build_wkt_polygon(vertices)

                # 6. Update Database
                with Session() as session:
                    session.execute(
                        Storm.__table__.update()
                        .where(Storm.sid == sid)
                        .values(cone_geom=func.ST_SetSRID(func.ST_GeomFromText(wkt), 4326))
                    )
                    session.commit()

                logger.info(f"✅ Successfully recalculated and updated cone for {sid}")

        logger.info("Finished refreshing all cones.")

    except Exception as e:
        logger.error(f"❌ Error during cone refresh: {e}", exc_info=True)


if __name__ == "__main__":
    refresh_all_cones()