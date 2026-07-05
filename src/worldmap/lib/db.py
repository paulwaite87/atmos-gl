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

    def get_volcanoes_as_geojson(self, vei_min, significant, date_codes):
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
            cur.execute(sql, (vei_min, significant, date_codes))
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

    def products_with_data(self, candidates):
        """Of `candidates`, return the subset that actually have catalogued field rows for
        the freshest (run_date, run_id) among those candidates. Used to base the scrubber
        range on which stepped products have DATA, not which are toggled on — so a layer
        being disabled (or enabled-but-not-yet-ingested) never nulls the whole timeline."""
        if not candidates:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_date, run_id
                FROM field_catalog
                WHERE product = ANY(%s)
                ORDER BY run_date DESC, run_id DESC
                LIMIT 1
                """,
                (list(candidates),),
            )
            row = cur.fetchone()
            if not row:
                return []
            d = (
                dict(row)
                if hasattr(row, "keys")
                else {"run_date": row[0], "run_id": row[1]}
            )
            cur.execute(
                """
                SELECT DISTINCT product
                FROM field_catalog
                WHERE run_date=%s AND run_id=%s AND product = ANY(%s)
                """,
                (d["run_date"], d["run_id"], list(candidates)),
            )
            return [
                (r["product"] if hasattr(r, "keys") else r[0]) for r in cur.fetchall()
            ]

    def get_latest_run_hours(self, products=None):
        """Return availability summary for the freshest (date, run) in the catalog.

        Args:
            products: optional list of product names to require. If given, an hour
                      counts as 'available' only when ALL listed products have it
                      (so the scrubber never lands on an hour some layer lacks), AND
                      the freshest run is resolved WITHIN those products only.

        The product scoping on the run-pick matters because the catalog is shared
        across independent model cycles that use different run identifiers: GFS runs
        00/06/12/18, RTOFS currents run "00", etc. Without scoping, a bare
        ORDER BY run_id DESC would mix models — e.g. an RTOFS row (run "00") on a
        newer date could outrank the latest GFS run, or the higher GFS run string
        could hide currents. Filtering the run-pick by `products` makes
        "latest run for {currents}" and "latest run for {GFS layers}" resolve to
        their own model's cycle.

        Returns dict:
            { "run_date": "20260613", "run_id": "18",
              "fmin": 0, "fmax": 23, "hours": [0,1,2,...,23], "n_products": 6 }
        or None if the catalog is empty.
        """
        with self.conn.cursor() as cur:
            # Freshest run = newest (run_date, run_id), scoped to the requested
            # products so unrelated model cycles can't outrank each other.
            if products:
                cur.execute(
                    """
                    SELECT run_date, run_id
                    FROM field_catalog
                    WHERE product = ANY(%s)
                    ORDER BY run_date DESC, run_id DESC
                    LIMIT 1
                """,
                    (list(products),),
                )
            else:
                cur.execute("""
                    SELECT run_date, run_id
                    FROM field_catalog
                    ORDER BY run_date DESC, run_id DESC
                    LIMIT 1
                """)
            row = cur.fetchone()
            if not row:
                return None
            d = (
                dict(row)
                if hasattr(row, "keys")
                else {"run_date": row[0], "run_id": row[1]}
            )
            run_date, run_id = d["run_date"], d["run_id"]

            if products:
                # Hours where the COUNT of distinct required products == len(products)
                cur.execute(
                    """
                    SELECT fhour
                    FROM field_catalog
                    WHERE run_date=%s AND run_id=%s AND product = ANY(%s)
                    GROUP BY fhour
                    HAVING COUNT(DISTINCT product) = %s
                    ORDER BY fhour
                """,
                    (run_date, run_id, list(products), len(products)),
                )
            else:
                cur.execute(
                    """
                    SELECT DISTINCT fhour
                    FROM field_catalog
                    WHERE run_date=%s AND run_id=%s
                    ORDER BY fhour
                """,
                    (run_date, run_id),
                )
            hours = [
                r[0] if not hasattr(r, "keys") else r["fhour"] for r in cur.fetchall()
            ]

        if not hours:
            return {
                "run_date": run_date,
                "run_id": run_id,
                "fmin": None,
                "fmax": None,
                "hours": [],
                "n_products": 0,
            }

        return {
            "run_date": run_date,
            "run_id": run_id,
            "fmin": hours[0],
            "fmax": hours[-1],
            "hours": hours,
        }

    def upsert_field_catalog(
        self,
        run_date: str,
        run_id: str,
        fhour: int,
        product: str,
        nlat: int,
        nlon: int,
        valid_time=None,
        storage_uri: str = None,
    ):
        """Upsert a field catalog row (metadata only, no arrays)."""
        sql = """
            INSERT INTO field_catalog
                (run_date, run_id, fhour, product, nlat, nlon, valid_time, storage_uri, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (run_date, run_id, fhour, product) DO UPDATE SET
                nlat=EXCLUDED.nlat,
                nlon=EXCLUDED.nlon,
                valid_time=EXCLUDED.valid_time,
                storage_uri=EXCLUDED.storage_uri,
                updated_at=now();
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        run_date,
                        run_id,
                        int(fhour),
                        product,
                        nlat,
                        nlon,
                        valid_time,
                        storage_uri,
                    ),
                )
        except Exception as e:
            logger.error(f"Error upserting field catalog: {e}")
            raise

    def get_field_catalog(
        self, run_date: str, run_id: str, fhour: int, product: str
    ) -> dict | None:
        """Fetch a catalog row (metadata only)."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_date, run_id, fhour, product, nlat, nlon, valid_time, updated_at, storage_uri
                FROM field_catalog
                WHERE run_date=%s AND run_id=%s AND fhour=%s AND product=%s
                """,
                (run_date, run_id, int(fhour), product),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def get_product_hours(self, run_date, run_id, product):
        """Return the sorted list of forecast hours present for one product in a run.

        Drives each task's per-hour render loop (the scrubber needs a PNG for every
        hour that has data). Cheap, indexed query against the catalog.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT fhour FROM field_catalog
                WHERE run_date=%s AND run_id=%s AND product=%s
                ORDER BY fhour
                """,
                (run_date, run_id, product),
            )
            return [
                r[0] if not hasattr(r, "keys") else r["fhour"] for r in cur.fetchall()
            ]

    def get_live_product_hours(self):
        """Return the set of all (product, fhour) pairs present anywhere in the
        catalog, across every run/date.

        Used by the housekeeper to identify orphaned per-hour render files: a
        rendered PNG for (layer, fhour) is orphaned only if NO catalog row anywhere
        has that product+fhour. Matching across all runs (not just the latest) is
        deliberate — during a run transition the catalog may briefly hold rows from
        more than one run, and we must not delete a file that a live row still backs.
        """
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT product, fhour FROM field_catalog")
            pairs = set()
            for r in cur.fetchall():
                if hasattr(r, "keys"):
                    pairs.add((r["product"], int(r["fhour"])))
                else:
                    pairs.add((r[0], int(r[1])))
            return pairs

    def field_catalog_exists(
        self, run_date: str, run_id: str, fhour: int, product: str
    ) -> bool:
        """Check if a catalog row exists (fast, indexed)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM field_catalog WHERE run_date=%s AND run_id=%s AND fhour=%s AND product=%s",
                (run_date, run_id, int(fhour), product),
            )
            return cur.fetchone() is not None

    def delete_field_catalog(
        self, run_date: str, run_id: str, fhour: int, product: str
    ):
        """Delete a catalog row."""
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM field_catalog WHERE run_date=%s AND run_id=%s AND fhour=%s AND product=%s",
                (run_date, run_id, int(fhour), product),
            )

    def get_orphan_field_rows(self, workdir_path) -> list:
        """Find catalog rows whose files are missing (for reconciliation).
        This is a helper for fieldstore.reconcile(). You'll need to pass
        the workdir Path and check which files exist on disk.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_date, run_id, fhour, product, storage_uri
                FROM field_catalog
                ORDER BY updated_at DESC
                """
            )
            rows = cur.fetchall()

        # Check which files exist

        orphans = []
        for row in rows:
            path = workdir_path / row["storage_uri"]
            if not path.exists():
                orphans.append(dict(row))
        return orphans

    def get_field_rows_except(self, run_date, run_id, products=None):
        """Return catalog rows NOT belonging to (run_date, run_id).

        Used by the data_collector to prune superseded runs (row + file). Each row
        includes storage_uri so the caller can unlink the file.

        When `products` is given, the query is additionally scoped to those products,
        so a collector pruning its own model's superseded runs cannot even see — let
        alone delete — another model's rows. The catalog is shared across independent
        cycles (GFS runs 00/06/12/18, RTOFS currents run "00"), so a bare "everything
        except this run" would span models; the filter keeps pruning within a family.
        """
        with self.conn.cursor() as cur:
            if products:
                cur.execute(
                    """
                    SELECT run_date, run_id, fhour, product, storage_uri
                    FROM field_catalog
                    WHERE NOT (run_date=%s AND run_id=%s)
                      AND product = ANY(%s)
                    """,
                    (run_date, run_id, list(products)),
                )
            else:
                cur.execute(
                    """
                    SELECT run_date, run_id, fhour, product, storage_uri
                    FROM field_catalog
                    WHERE NOT (run_date=%s AND run_id=%s)
                    """,
                    (run_date, run_id),
                )
            return [dict(r) for r in cur.fetchall()]

    def get_expired_field_rows(self, expiry_hours=48):
        """Return catalog rows older than expiry_hours.
        Used by the housekeeper to expire stale fields (row + file).
        Each row includes storage_uri so the caller can unlink the file.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_date, run_id, fhour, product, storage_uri
                FROM field_catalog
                WHERE updated_at < now() - make_interval(hours => %s)
                """,
                (int(expiry_hours),),
            )
            return [dict(r) for r in cur.fetchall()]

    def prune_field_catalog(self, expiry_hours: int = 48):
        """Delete catalog rows (and their files should be deleted separately) older than threshold."""
        self.execute(
            "DELETE FROM field_catalog WHERE updated_at < now() - make_interval(hours => %s)",
            (int(expiry_hours),),
        )

    def execute(self, sql, params=None):
        """Generic execution helper for simple queries (like manual deletes)."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def enqueue_backfill(self, run_date, run_id, fhour, product):
        """Record a missing-data request. Idempotent: a key already in the queue is left
        as-is (so repeated 404 flags collapse to one row), EXCEPT a previously 'failed'
        row is reset to 'requested' to allow a fresh attempt if the client asks again
        (e.g. the upstream run may have published the step since)."""
        sql = """
            INSERT INTO backfill_requests (run_date, run_id, fhour, product, status)
            VALUES (%s, %s, %s, %s, 'requested')
            ON CONFLICT (run_date, run_id, fhour, product) DO UPDATE SET
                status = CASE WHEN backfill_requests.status = 'failed'
                              THEN 'requested' ELSE backfill_requests.status END,
                updated_at = now();
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (run_date, run_id, int(fhour), product))
        except Exception as e:
            logger.error(f"Error enqueuing backfill: {e}")
            raise

    def claim_backfill_requests(self, limit=20):
        """Atomically claim up to `limit` pending requests, flipping them to 'fetching'
        so a second collector pass (or a stuck one) doesn't double-process them. Returns
        the claimed rows as dicts. Uses SKIP LOCKED for safe concurrent draining."""
        sql = """
            UPDATE backfill_requests SET status = 'fetching', attempts = attempts + 1,
                                         updated_at = now()
            WHERE (run_date, run_id, fhour, product) IN (
                SELECT run_date, run_id, fhour, product FROM backfill_requests
                WHERE status = 'requested'
                ORDER BY requested_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING run_date, run_id, fhour, product, attempts;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (limit,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"Error claiming backfill requests: {e}")
            return []

    def mark_backfill(self, run_date, run_id, fhour, product, status):
        """Set the terminal/intermediate status of a request ('done' | 'failed' |
        'requested')."""
        sql = """
            UPDATE backfill_requests SET status = %s, updated_at = now()
            WHERE run_date=%s AND run_id=%s AND fhour=%s AND product=%s;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, (status, run_date, run_id, int(fhour), product))
        except Exception as e:
            logger.error(f"Error marking backfill {status}: {e}")

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
