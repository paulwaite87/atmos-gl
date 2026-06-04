import os
import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from .shipping import get_vessel_class_from_type

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        # We fetch variables and provide defaults just in case
        db_user = os.getenv("PGUSER", "wmap")
        db_pass = os.getenv("PGPASSWORD", "wmap")
        db_name = os.getenv("PGDATABASE", "worldmap")
        db_host = os.getenv("PGHOST", "worldmap_db")
        db_port = os.getenv("PGPORT", "5432")

        try:
            self.conn = psycopg2.connect(
                user=db_user,
                password=db_pass,
                dbname=db_name,
                host=db_host,
                port=db_port,
                cursor_factory=RealDictCursor,
            )
            self.conn.autocommit = True
        except Exception as e:
            logger.error(f"Postgres Connection Failed: {e}")
            raise

    def update_ship_static_data(self, mmsi, metadata, body):
        """Processes ShipStaticData and UPSERTs into the ships table."""
        name = metadata.get("ShipName", "Unknown").strip()
        destination = body.get("Destination", "").strip()
        v_type = body.get("Type", 0)
        v_class = get_vessel_class_from_type(v_type)
        imo = body.get("ImoNumber", 0)
        callsign = body.get("CallSign", "").strip()
        draught = float(body.get("MaximumStaticDraught", 0.0))

        # Handle Dimension Math (AIS gives offsets A, B, C, D)
        dim = body.get("Dimension", {})
        length = int(dim.get("A", 0)) + int(dim.get("B", 0))
        beam = int(dim.get("C", 0)) + int(dim.get("D", 0))

        sql = """
              INSERT INTO ships (mmsi, name, 'destination', vessel_type, vessel_class, imo, callsign, draught, prev_draught, length, beam)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0.0, %s, %s) ON CONFLICT (mmsi) DO \
              UPDATE SET
                  prev_draught = CASE \
                  WHEN ships.draught != EXCLUDED.draught AND EXCLUDED.draught > 0 \
                  THEN ships.draught \
                  ELSE ships.prev_draught
              END \
              ,
                name = EXCLUDED.name,
                destination = EXCLUDED.destination,
                vessel_type = EXCLUDED.vessel_type,
                vessel_class = EXCLUDED.vessel_class,
                imo = EXCLUDED.imo,
                callsign = EXCLUDED.callsign,
                draught = EXCLUDED.draught,
                length = EXCLUDED.length,
                beam = EXCLUDED.beam; \
              """
        with self.conn.cursor() as cur:
            cur.execute(
                sql, (str(mmsi), name, destination, v_type, v_class, imo, callsign, draught, length, beam)
            )

    def update_ship_position_data(self, mmsi, body):
        lat = body.get("Latitude")
        lon = body.get("Longitude")
        nav_status = body.get("NavigationalStatus", 0)
        cog = body.get("Cog", 0.0)
        sog = body.get("Sog", 0.0)

        # Ensure the ship exists in the 'ships' table first (Shadow Insert)
        # This prevents Foreign Key violations in the history table.
        sql_ensure_ship = """
                          INSERT INTO ships (mmsi, name, vessel_type)
                          VALUES (%s, 'Unknown', 0) ON CONFLICT (mmsi) DO NOTHING; \
                          """

        # Update current ship status
        sql_live = """
                   UPDATE ships
                   SET lat                  = %s,
                       lon                  = %s,
                       geom                 = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                       nav_status           = %s,
                       cog                  = %s,
                       sog                  = %s,
                       last_position_update = NOW()
                   WHERE mmsi = %s; \
                   """

        # Insert historical track
        sql_history = """
                      INSERT INTO ship_position (mmsi, lat, lon, geom, sog, cog, nav_status, acquired_at)
                      VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, NOW());
                      """
        try:
            with self.conn.cursor() as cur:
                # Step 1: Guarantee the parent record exists
                cur.execute(sql_ensure_ship, (str(mmsi),))

                # Step 2: Update live position
                cur.execute(
                    sql_live, (lat, lon, lon, lat, nav_status, cog, sog, str(mmsi))
                )

                # Step 3: Record history (now safe from FK errors)
                cur.execute(
                    sql_history, (str(mmsi), lat, lon, lon, lat, sog, cog, nav_status)
                )
        except Exception as e:
            logger.error(f"Database error updating position for {mmsi}: {e}")

    def get_region_definition(self, label):
        """Fetches the bounding box for a specific region label."""
        sql = """
              SELECT ST_XMin(boundary) as lon_min, \
                     ST_YMin(boundary) as lat_min,
                     ST_XMax(boundary) as lon_max, \
                     ST_YMax(boundary) as lat_max
              FROM map_region \
              WHERE label = %s;
              """
        with self.conn.cursor() as cur:
            cur.execute(sql, (label,))
            return cur.fetchone()

    def get_current_ship_total(self):
        """Returns the total number of ships currently in the database."""
        sql = "SELECT COUNT(*) as total FROM ships;"
        with self.conn.cursor() as cur:
            cur.execute(sql)
            result = cur.fetchone()
            return result["total"] if result else 0

    def get_fleet_as_geojson(self):
        sql = """
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'type',       'Feature',
                            'geometry',   ST_AsGeoJSON(geom)::jsonb,
                            'properties', jsonb_build_object(
                                'mmsi', mmsi,
                                'name', name,
                                'heading', COALESCE(cog, 0.0),
                                'length', COALESCE(length, 0),
                                'vessel_type', COALESCE(vessel_type, 0),
                                'destination', COALESCE(destination, 'Unknown'),
                                'vessel_class', COALESCE(vessel_class, 'Unknown'),
                                'imo', COALESCE(imo, 0),
                                'callsign', COALESCE(callsign, 'N/A'),
                                'draught', COALESCE(draught, 0.0)
                            )
                        )
                    ),
                    '[]'::jsonb
                )
            )::text AS geojson
            FROM ships
            WHERE geom IS NOT NULL;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                result = cur.fetchone()

                # Unpack explicitly by the alias key
                if result and "geojson" in result:
                    return result["geojson"]
                return '{"type":"FeatureCollection","features":[]}'

        except Exception as e:
            logger.error(f"Error building native fleet GeoJSON layer: {e}")
            return '{"type":"FeatureCollection","features":[]}'

    def get_ships(self, map_region_name=None, expiry_days=3):
        """
        Retrieves ships updated within expiry_days.
        Filters by spatial region labels if provided, else returns global.
        """
        if map_region_name:
            # Use the direct equality check for the label
            # and use the INTERVAL '1 day' * %s math to safely inject the number of days
            sql = """
                  SELECT DISTINCT s.*
                  FROM ships s
                           JOIN map_region r ON ST_Contains(r.boundary, s.geom)
                  WHERE r.label = %s
                    AND s.last_position_update > NOW() - (INTERVAL '1 day' * %s)
                    AND s.lat IS NOT NULL
                    AND s.lon IS NOT NULL;
                  """
            params = (map_region_name, int(expiry_days))
        else:
            sql = """
                  SELECT * \
                  FROM ships s
                  WHERE s.last_position_update > NOW() - INTERVAL '%s days'
                    AND s.geom IS NOT NULL
                    AND s.lat IS NOT NULL
                    AND s.lon IS NOT NULL; \
                  """
            params = (expiry_days,)

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def is_in_region(self, lat, lon, region_label):
        """Quick boolean check if a point is inside a specific region."""
        sql = """
              SELECT 1 \
              FROM map_region
              WHERE label = %s
                AND ST_Contains(boundary, ST_SetSRID(ST_MakePoint(%s, %s), 4326)); \
              """
        with self.conn.cursor() as cur:
            cur.execute(sql, (region_label, lon, lat))
            return cur.fetchone() is not None

    def __del__(self):
        if hasattr(self, "conn"):
            self.conn.close()

    def get_ship_track(self, mmsi, limit=100):
        """
        Retrieves historical positions for a specific ship, newest first.
        Includes a protective check to return an empty track if MMSI is missing.
        """
        if not mmsi:
            return []

        sql = """
            SELECT lat, lon FROM ship_position 
            WHERE mmsi = %s 
            ORDER BY acquired_at DESC 
            LIMIT %s;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (str(mmsi), limit))
                # Returns an empty list [] if no rows are found
                return cur.fetchall() or []
        except Exception as e:
            logger.error(f"Error fetching track for MMSI {mmsi}: {e}")
            return []

    def prune_vessel_tracks(self, expiry_days):
        """Removes position history older than the specified number of days."""
        if not expiry_days or expiry_days <= 0:
            return 0

        sql = """
              DELETE \
              FROM ship_position
              WHERE acquired_at < NOW() - INTERVAL '%s days'; \
              """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (expiry_days,))
                deleted_rows = cur.rowcount
                if deleted_rows > 0:
                    logger.info(f"Pruned {deleted_rows} old position records.")
                return deleted_rows
        except Exception as e:
            logger.error(f"Error pruning vessel tracks: {e}")
            return 0

    def update_lightning_strike(self, strike_id, lat, lon, quality, timestamp_iso):
        """UPSERTs a lightning strike into the database with spatial geometry."""
        sql = """
              INSERT INTO lightning_strikes (id, lat, lon, geom, quality, acquired_at)
              VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s) ON CONFLICT (id) DO NOTHING; \
              """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    sql, (strike_id, lat, lon, lon, lat, quality, timestamp_iso)
                )
        except Exception as e:
            logger.error(f"Error saving lightning strike {strike_id}: {e}")

    def get_lightning_in_region(
        self, lon_min, lat_min, lon_max, lat_max, expiry_minutes=60
    ):
        """Retrieves strikes within a specific bbox and time window."""
        sql = """
              SELECT lat, lon, acquired_at as timestamp
              FROM lightning_strikes
              WHERE geom && ST_MakeEnvelope(%s \
                  , %s \
                  , %s \
                  , %s \
                  , 4326)
                AND acquired_at \
                  > NOW() - (INTERVAL '1 minute' * %s); \
              """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (lon_min, lat_min, lon_max, lat_max, expiry_minutes))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching lightning for region: {e}")
            return []

    def prune_lightning(self, expiry_hours=24):
        """Deletes old lightning data to keep the table performant."""
        sql = "DELETE FROM lightning_strikes WHERE acquired_at < NOW() - (INTERVAL '1 hour' * %s);"
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (expiry_hours,))
                return cur.rowcount
        except Exception as e:
            logger.error(f"Error pruning lightning: {e}")
            return 0

    def get_priority_region_list(self, primary_region_label):
        """
        Returns all regions from the database, ordered so the primary_region_label
        is first. Includes bounding box coordinates.
        """
        sql = """
              SELECT label,
                     ST_XMin(boundary) as lon_min,
                     ST_YMin(boundary) as lat_min,
                     ST_XMax(boundary) as lon_max,
                     ST_YMax(boundary) as lat_max
              FROM map_region
              ORDER BY (label = %s) DESC, label ASC; \
              """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (primary_region_label,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching priority region list: {e}")
            return []

    def execute(self, sql, params=None):
        """Generic execution helper for simple queries (like manual deletes)."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
