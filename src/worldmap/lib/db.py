import os
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

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
