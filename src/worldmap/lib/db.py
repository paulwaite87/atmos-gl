import os
import psycopg2
from psycopg2.extras import RealDictCursor
import numpy as np
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
              INSERT INTO ships (mmsi, name, destination, vessel_type, vessel_class, imo, callsign, draught, prev_draught, length, beam)
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
                sql,
                (
                    str(mmsi),
                    name,
                    destination,
                    v_type,
                    v_class,
                    imo,
                    callsign,
                    draught,
                    length,
                    beam,
                ),
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

    def get_lightning_as_geojson(self, expiry_hours=2):
        """Returns lightning strikes within the expiry window as a GeoJSON string."""
        sql = """
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'type',       'Feature',
                            'geometry',   ST_AsGeoJSON(geom)::jsonb,
                            'properties', jsonb_build_object(
                                'id', id,
                                'quality', quality,
                                'age_minutes', EXTRACT(EPOCH FROM (NOW() - acquired_at)) / 60.0,
                                'timestamp', to_char(acquired_at, 'HH24:MI')
                            )
                        )
                    ),
                    '[]'::jsonb
                )
            )::text AS geojson
            FROM lightning_strikes
            WHERE acquired_at >= NOW() - (INTERVAL '1 hour' * %s);
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (expiry_hours,))
                result = cur.fetchone()
                if result and "geojson" in result:
                    return result["geojson"]
                return '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building lightning GeoJSON: {e}")
            return '{"type":"FeatureCollection","features":[]}'

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

    def update_quake(self, quake_id, mag, depth, place, time_iso, lat, lon):
        """UPSERTs an earthquake into the database."""
        sql = """
            INSERT INTO earthquakes (id, mag, depth, place, eq_time, lat, lon, geom)
            VALUES (%s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT (id) DO UPDATE SET
                mag = EXCLUDED.mag,
                depth = EXCLUDED.depth,
                place = EXCLUDED.place,
                eq_time = EXCLUDED.eq_time;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    sql, (quake_id, mag, depth, place, time_iso, lat, lon, lon, lat)
                )
        except Exception as e:
            logger.error(f"Error saving earthquake {quake_id}: {e}")

    def get_quakes_as_geojson(self, min_mag=3.5, expiry_hours=12, recent_hours=3):
        """Returns earthquakes as GeoJSON, filtering by age and magnitude."""
        sql = """
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'type',       'Feature',
                            'geometry',   ST_AsGeoJSON(geom)::jsonb,
                            'properties', jsonb_build_object(
                                'id', id,
                                'mag', mag,
                                'depth', depth,
                                'place', place,
                                'age_minutes', EXTRACT(EPOCH FROM (NOW() - eq_time)) / 60.0,
                                'is_recent', (EXTRACT(EPOCH FROM (NOW() - eq_time)) / 3600.0) <= %s
                            )
                        )
                    ),
                    '[]'::jsonb
                )
            )::text AS geojson
            FROM earthquakes
            WHERE eq_time >= NOW() - (INTERVAL '1 hour' * %s)
              AND mag >= %s;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (recent_hours, expiry_hours, min_mag))
                result = cur.fetchone()
                if result and "geojson" in result:
                    return result["geojson"]
                return '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building quake GeoJSON: {e}")
            return '{"type":"FeatureCollection","features":[]}'

    def update_volcano(self, v_id, name, lat, lon, vei, significant, date_code):
        sql = """
            INSERT INTO volcanoes (id, name, lat, lon, vei, significant, erupt_date_code, geom)
            VALUES (%s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT (id) DO UPDATE SET
                vei = EXCLUDED.vei,
                significant = EXCLUDED.significant,
                erupt_date_code = EXCLUDED.erupt_date_code;
        """
        with self.conn.cursor() as cur:
            cur.execute(
                sql, (v_id, name, lat, lon, vei, significant, date_code, lon, lat)
            )

    def get_volcanoes_as_geojson(self, vei_min, significant_only, date_codes):
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
                                'vei', vei,
                                'code', erupt_date_code
                            )
                        )
                    ), '[]'::jsonb
                )
            )::text AS geojson -- Added explicit alias
            FROM volcanoes
            WHERE vei >= %s
              AND (%s = FALSE OR significant = TRUE)
              AND erupt_date_code = ANY(%s);
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (vei_min, significant_only, date_codes))
            result = cur.fetchone()

            # Unpack by dictionary key to comply with RealDictCursor
            if result and "geojson" in result:
                return result["geojson"]
            return '{"type":"FeatureCollection","features":[]}'

    def update_storm(self, sid, name, cone_vertices, track_points):
        """
        Updates the master storm record and completely refreshes its track history/forecast.
        cone_vertices: list of (lon, lat) tuples defining the error cone.
        track_points: list of dicts with keys: LAT, LON, TIME, TYPE, TAU.
        """
        # 1. Convert Python cone vertices into a PostGIS Polygon WKT string
        cone_wkt = None
        if cone_vertices and len(cone_vertices) >= 3:
            # PostGIS requires polygons to be closed (first point == last point)
            if cone_vertices[0] != cone_vertices[-1]:
                cone_vertices.append(cone_vertices[0])
            coords = ",".join([f"{lon} {lat}" for lon, lat in cone_vertices])
            cone_wkt = f"POLYGON(({coords}))"

        sql_storm = """
            INSERT INTO storms (sid, name, cone_geom, updated_at)
            VALUES (%s, %s, ST_GeomFromText(%s, 4326), NOW())
            ON CONFLICT (sid) DO UPDATE SET
                name = EXCLUDED.name,
                cone_geom = EXCLUDED.cone_geom,
                updated_at = NOW();
        """

        # We delete old tracks to prevent duplicate forecast points building up over time
        sql_delete_tracks = "DELETE FROM storm_track WHERE sid = %s;"

        sql_insert_track = """
            INSERT INTO storm_track (sid, record_type, dt, tau, lat, lon, geom)
            VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326));
        """

        try:
            with self.conn.cursor() as cur:
                # Update Master Record
                cur.execute(sql_storm, (sid, name, cone_wkt))
                # Wipe old track points
                cur.execute(sql_delete_tracks, (sid,))
                # Insert fresh points
                for pt in track_points:
                    cur.execute(
                        sql_insert_track,
                        (
                            sid,
                            pt["TYPE"],
                            pt.get(
                                "TIME"
                            ),  # Might be None for forecast points depending on your parser
                            pt.get("TAU", 0),
                            pt["LAT"],
                            pt["LON"],
                            pt["LON"],
                            pt["LAT"],
                        ),
                    )
        except Exception as e:
            logger.error(
                f"❌ Error updating storm {sid} in database: {e}", exc_info=True
            )

    def update_storm_cone(self, sid, cone_vertices):
        """Updates only the cone geometry for a specific storm."""
        # Convert vertices to JSON string for Postgres
        import json

        vertices_json = json.dumps(cone_vertices)

        sql = """
            UPDATE storms 
            SET cone_vertices = ST_GeomFromGeoJSON(%s)
            WHERE sid = %s;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (vertices_json, sid))
                logger.info(f"Retrospectively updated cone for storm {sid}")
        except Exception as e:
            logger.error(f"Error updating cone for {sid}: {e}")

    def get_storms_as_geojson(self):
        """Compiles active storms, tracks, and cones into a single GeoJSON FeatureCollection."""
        sql = """
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
            )::text AS geojson
            FROM (
                -- 1. Error Cones (Polygons)
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(cone_geom)::jsonb,
                    'properties', jsonb_build_object('feature_type', 'CONE', 'sid', sid, 'name', name)
                ) AS feature 
                FROM storms 
                WHERE cone_geom IS NOT NULL

                UNION ALL

                -- 2. Past Track Lines (Solid)
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(ST_MakeLine(geom ORDER BY dt))::jsonb,
                    'properties', jsonb_build_object('feature_type', 'TRACK_PAST', 'sid', sid)
                ) AS feature 
                FROM storm_track 
                WHERE record_type IN ('PAST', 'CURRENT') 
                GROUP BY sid HAVING count(geom) > 1

                UNION ALL

                -- 3. Forecast Track Lines (Dashed)
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(ST_MakeLine(geom ORDER BY dt))::jsonb,
                    'properties', jsonb_build_object('feature_type', 'TRACK_FORECAST', 'sid', sid)
                ) AS feature 
                FROM storm_track 
                WHERE record_type IN ('CURRENT', 'FORECAST') 
                GROUP BY sid HAVING count(geom) > 1

                UNION ALL

                -- 4. Individual Points (for hover data)
                SELECT jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(t.geom)::jsonb,
                    'properties', jsonb_build_object(
                        'feature_type', 'POINT',
                        'sid', t.sid,
                        'name', s.name,
                        'record_type', t.record_type,
                        'tau', t.tau,
                        'dt', t.dt
                    )
                ) AS feature 
                FROM storm_track t
                JOIN storms s ON t.sid = s.sid
            ) subquery;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                result = cur.fetchone()
                if result and "geojson" in result:
                    return result["geojson"]
        except Exception as e:
            logger.error(f"Error fetching storms geojson: {e}")
        return '{"type":"FeatureCollection","features":[]}'

    def prune_expired_storms(self, expiry_days=4):
        """Removes storms that haven't been updated recently."""
        sql = "DELETE FROM storms WHERE updated_at < NOW() - (INTERVAL '1 day' * %s);"
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (expiry_days,))
                if cur.rowcount > 0:
                    logger.info(f"Pruned {cur.rowcount} expired storms from database.")
        except Exception as e:
            logger.error(f"Error pruning expired storms: {e}")

    def update_satellite(self, norad_id, name, omm, epoch_iso):
        from psycopg2.extras import Json

        sql = """
            INSERT INTO satellites (norad_id, name, omm, epoch, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (norad_id) DO UPDATE SET
                name = EXCLUDED.name,
                omm = EXCLUDED.omm,
                epoch = EXCLUDED.epoch, 
                updated_at = NOW();
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (norad_id, name, Json(omm), epoch_iso))

    def get_satellites_by_names(self, names):
        if not names:
            return []
        sql = "SELECT norad_id, name, omm, epoch FROM satellites WHERE name = ANY(%s);"
        with self.conn.cursor() as cur:
            cur.execute(sql, (list(names),))
            return (
                cur.fetchall()
            )  # RealDictCursor returns omm already decoded to a dict

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

    def gfs_grib_exists(self, gfs_date, gfs_run, fhour, product):
        """Lightweight existence check (no blob transfer)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM gfs_cache "
                "WHERE gfs_date=%s AND gfs_run=%s AND fhour=%s AND product=%s",
                (gfs_date, gfs_run, int(fhour), product),
            )
            return cur.fetchone() is not None

    def field_exists(self, gfs_date, gfs_run, fhour, product):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM layer_data "
                "WHERE gfs_date=%s AND gfs_run=%s AND fhour=%s AND product=%s",
                (gfs_date, gfs_run, int(fhour), product),
            )
            return cur.fetchone() is not None

    @staticmethod
    def _flat(field):
        """2-D field -> flat python list of floats (row-major), or None."""
        if field is None:
            return None
        return np.asarray(field, dtype=np.float32).ravel().tolist()

    def store_field(self, gfs_date, gfs_run, fhour, product, unpacked, valid_time=None):
        """UPSERT a pre-processed field set (dict from worldmap.lib.unpack)."""
        lat = np.asarray(unpacked["lat"], dtype=np.float64)
        lon = np.asarray(unpacked["lon"], dtype=np.float64)
        nlat, nlon = int(lat.shape[0]), int(lon.shape[0])
        sql = """
            INSERT INTO layer_data
                (gfs_date, gfs_run, fhour, product, nlat, nlon,
                 lat, lon, vals, vals2, u, v, valid_time, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (gfs_date, gfs_run, fhour, product) DO UPDATE SET
                nlat=EXCLUDED.nlat, nlon=EXCLUDED.nlon,
                lat=EXCLUDED.lat, lon=EXCLUDED.lon,
                vals=EXCLUDED.vals, vals2=EXCLUDED.vals2,
                u=EXCLUDED.u, v=EXCLUDED.v,
                valid_time=EXCLUDED.valid_time, updated_at=now();
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (
                    gfs_date, gfs_run, int(fhour), product, nlat, nlon,
                    lat.tolist(), lon.tolist(),
                    self._flat(unpacked.get("values")),
                    self._flat(unpacked.get("values2")),
                    self._flat(unpacked.get("u")),
                    self._flat(unpacked.get("v")),
                    valid_time,
                ))
        except Exception as e:
            logger.error(
                f"Error storing field {gfs_date}/{gfs_run}/f{int(fhour):03d}/{product}: {e}"
            )

    def get_field(self, gfs_date, gfs_run, fhour, product):
        """Return the field set as numpy arrays, or None if absent.

        { 'lat': 1-D, 'lon': 1-D, 'values'|'values2'|'u'|'v': 2-D (nlat,nlon) or None,
          'valid_time': datetime }
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT nlat,nlon,lat,lon,vals,vals2,u,v,valid_time FROM layer_data "
                "WHERE gfs_date=%s AND gfs_run=%s AND fhour=%s AND product=%s",
                (gfs_date, gfs_run, int(fhour), product),
            )
            row = cur.fetchone()
        if not row:
            return None
        nlat, nlon = row["nlat"], row["nlon"]

        def grid(key):
            a = row[key]
            if a is None:
                return None
            return np.asarray(a, dtype=np.float32).reshape(nlat, nlon)

        return {
            "lat": np.asarray(row["lat"], dtype=np.float64),
            "lon": np.asarray(row["lon"], dtype=np.float64),
            "values": grid("vals"),
            "values2": grid("vals2"),
            "u": grid("u"),
            "v": grid("v"),
            "valid_time": row["valid_time"],
        }

    def prune_layer_data_except(self, gfs_date, gfs_run):
        self.execute(
            "DELETE FROM layer_data WHERE NOT (gfs_date=%s AND gfs_run=%s)",
            (gfs_date, gfs_run),
        )

    def prune_layer_data(self, expiry_hours=48):
        self.execute(
            "DELETE FROM layer_data WHERE updated_at < now() - make_interval(hours => %s)",
            (int(expiry_hours),),
        )

    def execute(self, sql, params=None):
        """Generic execution helper for simple queries (like manual deletes)."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
