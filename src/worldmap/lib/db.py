import os
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
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

    # ---- markers (place markers + sampled weather) --------------------------
    def upsert_markers(self, rows):
        """Bulk-upsert marker STATIC fields from the geojson. Each row is a dict with
        keys: id, name, kind, country, priority, pop, capital, color, timezone, lat, lon.
        Deliberately does NOT touch the wx_* columns, so a re-import preserves the last
        sampled weather."""
        if not rows:
            return
        sql = """
            INSERT INTO markers
                (id, name, kind, country, priority, pop, capital, color, timezone,
                 lat, lon, geom)
            VALUES
                (%(id)s, %(name)s, %(kind)s, %(country)s, %(priority)s, %(pop)s,
                 %(capital)s, %(color)s, %(timezone)s, %(lat)s, %(lon)s,
                 ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326))
            ON CONFLICT (id) DO UPDATE SET
                name      = EXCLUDED.name,
                kind      = EXCLUDED.kind,
                country   = EXCLUDED.country,
                priority  = EXCLUDED.priority,
                pop       = EXCLUDED.pop,
                capital   = EXCLUDED.capital,
                color     = EXCLUDED.color,
                timezone  = EXCLUDED.timezone,
                lat       = EXCLUDED.lat,
                lon       = EXCLUDED.lon,
                geom      = EXCLUDED.geom;
        """
        with self.conn.cursor() as cur:
            execute_batch(cur, sql, rows, page_size=500)

    def delete_markers_not_in(self, ids):
        """Delete markers whose id is NOT in `ids` (i.e. removed from the geojson).
        Returns the number of rows deleted. Guarded by the caller against an empty list
        so a failed geojson read can't wipe the table."""
        if not ids:
            return 0
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM markers WHERE NOT (id = ANY(%s));", (list(ids),))
            return cur.rowcount

    def update_marker_weather(self, updates):
        """Bulk-update the wx_* weather columns. Each update is a dict with keys:
        id, t (deg C), rh (%), ws (m/s), wd (deg from), valid_time (ISO str or None).
        Rows not matched (id absent) are simply no-ops."""
        if not updates:
            return
        sql = """
            UPDATE markers SET
                wx_temp_c       = %(t)s,
                wx_humidity_pct = %(rh)s,
                wx_wind_ms      = %(ws)s,
                wx_wind_dir_deg = %(wd)s,
                wx_valid_time   = %(valid_time)s,
                wx_updated_at   = now()
            WHERE id = %(id)s;
        """
        with self.conn.cursor() as cur:
            execute_batch(cur, sql, updates, page_size=500)

    def get_markers_as_geojson(self):
        """All markers as a GeoJSON FeatureCollection, static fields + current weather
        folded into properties (weather keys are null where not yet sampled)."""
        sql = """
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON(geom)::jsonb,
                            'properties', jsonb_build_object(
                                'name', name,
                                'kind', kind,
                                'country', country,
                                'priority', priority,
                                'pop', pop,
                                'capital', capital,
                                'color', color,
                                'timezone', timezone,
                                't', wx_temp_c,
                                'rh', wx_humidity_pct,
                                'ws', wx_wind_ms,
                                'wd', wx_wind_dir_deg,
                                'wx_valid_time', wx_valid_time
                            )
                        )
                    ),
                    '[]'::jsonb
                )
            )::text AS geojson
            FROM markers;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                result = cur.fetchone()
                if result and "geojson" in result:
                    return result["geojson"]
                return '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building markers GeoJSON: {e}")
            return '{"type":"FeatureCollection","features":[]}'
