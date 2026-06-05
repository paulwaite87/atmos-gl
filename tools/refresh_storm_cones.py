#!/usr/bin/env python3
import logging

# Import your core modules based on the project structure
from worldmap.lib.db import Database
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
    db = Database()
    config = WorldMapConfig(config_path="/opt/project/config/worldmap.json")
    updater = StormUpdater(config, MapData(config))

    try:
        # 2. Get all Storm IDs
        with db.conn.cursor() as cur:
            cur.execute("SELECT sid FROM storms")
            storms = cur.fetchall()

        if not storms:
            logger.info("No active storms found in the database.")
            return

        logger.info(f"Found {len(storms)} storms. Rebuilding cones...")

        for storm in storms:
            # Handle RealDictCursor output
            sid = storm['sid'] if isinstance(storm, dict) else storm[0]

            with db.conn.cursor() as cur:
                # 3. Fetch forecast points AND the current point for this storm
                cur.execute("""
                    SELECT lat, lon, COALESCE(tau, 0) as tau 
                    FROM storm_track 
                    WHERE sid = %s AND record_type IN ('CURRENT', 'FORECAST') 
                    ORDER BY COALESCE(tau, 0) ASC
                """, (sid,))
                track_data = cur.fetchall()

            if len(track_data) < 2:
                logger.debug(f"Skipping {sid}: Not enough forecast points to build a cone.")
                continue

            # 4. Format for the StormUpdater calculator
            forecast_points = []
            for r in track_data:
                lat = float(r['lat']) if isinstance(r, dict) else float(r[0])
                lon = float(r['lon']) if isinstance(r, dict) else float(r[1])
                tau = int(r['tau']) if isinstance(r, dict) else int(r[2])
                forecast_points.append({"LAT": lat, "LON": lon, "TAU": tau})

            # 5. Calculate Geometry using your official class logic
            vertices = updater._build_cone_polygons(forecast_points)

            if vertices:
                wkt = build_wkt_polygon(vertices)

                # 6. Update Database
                with db.conn.cursor() as cur:
                    cur.execute("""
                        UPDATE storms 
                        SET cone_geom = ST_SetSRID(ST_GeomFromText(%s), 4326) 
                        WHERE sid = %s
                    """, (wkt, sid))

                logger.info(f"✅ Successfully recalculated and updated cone for {sid}")

        logger.info("Finished refreshing all cones.")

    except Exception as e:
        logger.error(f"❌ Error during cone refresh: {e}", exc_info=True)


if __name__ == "__main__":
    refresh_all_cones()