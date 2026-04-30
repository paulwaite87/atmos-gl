import os
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

def get_vessel_class(vessel):
    vessel_type = int(vessel["vessel_type"]) or 0

    # Specific/Specialized Codes
    special_vessel_types = {
         0: "Vessel",
        30: "Fishing Vessel",
        31: "Towing Vessel",
        32: "Towing (Large/Towed)",
        33: "Dredging/Underwater Ops",
        34: "Diving Ops",
        35: "Military Ops",
        36: "Sailing Vessel",
        37: "Pleasure Craft",
        50: "Pilot Vessel",
        51: "Search and Rescue (SAR)",
        52: "Tug",
        53: "Port Tender",
        54: "Anti-Pollution Equipment",
        55: "Law Enforcement",
        58: "Medical Transport",
        59: "Non-Combatant (Neutral State)",
    }

    if vessel_type in special_vessel_types:
        return special_vessel_types[vessel_type]

    vessel_classes = {
        1: "WIG (Wing In Ground)",
        2: "WIG (Wing In Ground)",
        4: "High Speed Craft",
        6: "Passenger",
        7: "Cargo",
        8: "Tanker",
        9: "Other",
    }

    class_digit = int(vessel_type // 10)
    return vessel_classes.get(class_digit, f"Vessel (Type {vessel_type})")

def get_expanded_vessel_class(vessel):
    """
    Expands the standard vessel classes to a more descriptive string, but
    only for Tankers and Passenger ships.
    Args:
        vessel: The vessel to find the expanded class for

    Returns: String expanded class, or the default standard class

    """
    vessel_class = get_vessel_class(vessel)
    length, beam = get_vessel_dimensions(vessel)

    if vessel_class == "Tanker":
        if length > 350 or beam > 60:
            return "ULTRA"
        elif length > 250:
            return "VLCC"
        elif length >= 180:
            return "STD"
        else:
            return vessel_class

    elif vessel_class == "Passenger":
        # Categorizing based on the 3 levels of Cruise Ship scale
        if length > 250:
            return "Cruise MEGA"  # Mega-Cruise / Super-Liner
        elif length > 150:
            return "Cruise"  # Standard Mid-Size Cruise
        elif length > 50:
            return "Ferry"  # Boutique / Large Ferry
        else:
            return vessel_class

    else:
        return vessel_class

def get_vessel_subclass(vessel):
    vessel_type = int(vessel["vessel_type"]) or 0

    if not vessel or vessel_type == 0:
        return ""

    vessel_subclasses = {
        1: " HazA",
        2: " HazB",
        3: " HazC",
        4: " HazD"
    }
    sub_digit = vessel_type % 10
    return vessel_subclasses.get(sub_digit, "")

def get_vessel_dimensions(vessel):
    length = int(vessel["length"]) or 0
    beam = int(vessel["beam"]) or 0
    return length, beam

def get_vessel_navigational_status(vessel):
    cog = float(vessel["cog"]) or 0.0
    sog = float(vessel["sog"]) or 0.0
    nav_status = int(vessel["nav_status"]) or 1
    return cog, sog, nav_status

def get_vessel_position(vessel):
    latitude = float(vessel["lat"]) or None
    longitude = float(vessel["lon"]) or None
    return latitude, longitude

def get_vessel_name(vessel):
    return vessel["name"].replace('"', "").strip()

def get_vessel_description(vessel):
    """This function returns the vessel description which starts with the
    name of the ship and then the class and subclass information"""
    vessel_name = get_vessel_name(vessel)
    vessel_class = get_expanded_vessel_class(vessel)
    vessel_subclass = get_vessel_subclass(vessel)

    return f"{vessel_name} {vessel_class}{vessel_subclass}"

def vessel_is_underway(vessel) -> bool:
    """
    Returns True if vessel is moving faster than 1 knot
    and is not anchored (1) or moored (5).
    """
    # 0 = Under way using engine, 8 = Under way sailing
    # 1 = Anchored, 5 = Moored
    cog, sog, nav_status = get_vessel_navigational_status(vessel)
    return sog > 1.0 and nav_status not in [1, 5]


class ShipDatabase:
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
                cursor_factory=RealDictCursor
            )
            self.conn.autocommit = True
        except Exception as e:
            logger.error(f"Postgres Connection Failed: {e}")
            raise

    def update_ship_static_data(self, mmsi, metadata, body):
        """Processes ShipStaticData and UPSERTs into the ships table."""
        name = metadata.get("ShipName", "Unknown").strip()
        v_type = body.get("Type", 0)
        imo = body.get("ImoNumber", 0)
        callsign = body.get("CallSign", "").strip()
        draught = float(body.get("MaximumStaticDraught", 0.0))

        # Handle Dimension Math (AIS gives offsets A, B, C, D)
        dim = body.get("Dimension", {})
        length = int(dim.get("A", 0)) + int(dim.get("B", 0))
        beam = int(dim.get("C", 0)) + int(dim.get("D", 0))

        sql = """
              INSERT INTO ships (mmsi, name, vessel_type, imo, callsign, draught, prev_draught, length, beam)
              VALUES (%s, %s, %s, %s, %s, %s, 0.0, %s, %s) ON CONFLICT (mmsi) DO \
              UPDATE SET
                  prev_draught = CASE \
                  WHEN ships.draught != EXCLUDED.draught AND EXCLUDED.draught > 0 \
                  THEN ships.draught \
                  ELSE ships.prev_draught
              END \
              ,
                name = EXCLUDED.name,
                vessel_type = EXCLUDED.vessel_type,
                imo = EXCLUDED.imo,
                callsign = EXCLUDED.callsign,
                draught = EXCLUDED.draught,
                length = EXCLUDED.length,
                beam = EXCLUDED.beam; \
              """
        with self.conn.cursor() as cur:
            cur.execute(sql, (str(mmsi), name, v_type, imo, callsign, draught, length, beam))

    def update_ship_position_data(self, mmsi, body):
        lat = body.get("Latitude")
        lon = body.get("Longitude")
        nav_status = body.get("NavigationalStatus")
        cog = body.get("Cog")
        sog = body.get("Sog")

        """Updates a ship's current location, spatial geometry, speed, course and nav status."""
        sql = """
              UPDATE ships
              SET lat                  = %s,
                  lon                  = %s,
                  geom                 = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                  nav_status           = %s,
                  cog                  = %s,
                  sog                  = %s,
                  last_position_update = NOW()
              WHERE mmsi = %s;
              """
        with self.conn.cursor() as cur:
            # We use nav_status here, which may be None if not provided
            cur.execute(sql, (lat, lon, lon, lat, nav_status, cog, sog, str(mmsi)))

    def get_current_ship_total(self):
        """Returns the total number of ships currently in the database."""
        sql = "SELECT COUNT(*) as total FROM ships;"
        with self.conn.cursor() as cur:
            cur.execute(sql)
            result = cur.fetchone()
            return result['total'] if result else 0

    def get_fleet(self, region_labels=None, expiry_days=3):
        """
        Retrieves ships updated within expiry_days.
        Filters by spatial region labels if provided, else returns global.
        """
        if region_labels and len(region_labels) > 0:
            sql = """
                  SELECT DISTINCT s.* \
                  FROM ships s \
                           JOIN ship_regions r ON ST_Contains(r.boundary, s.geom)
                  WHERE r.label = ANY (%s)
                    AND s.last_position_update > NOW() - INTERVAL '%s days'
                    AND s.lat IS NOT NULL
                    AND s.lon IS NOT NULL; \
                  """
            params = (region_labels, expiry_days)
        else:
            sql = """
                  SELECT * \
                  FROM ships
                  WHERE last_position_update > NOW() - INTERVAL '%s days'
                    AND geom IS NOT NULL
                    AND s.lat IS NOT NULL
                    AND s.lon IS NOT NULL; \
                  """
            params = (expiry_days,)

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def get_active_bboxes(self, region_labels=None):
        """
        Returns a list of [lat_s, lon_w, lat_n, lon_e] for the Harvester.
        Defaults to Global World if no regions specified.
        """
        if region_labels and len(region_labels) > 0:
            sql = """
                  SELECT ST_YMin(env), \
                         ST_XMin(env), \
                         ST_YMax(env), \
                         ST_XMax(env)
                  FROM (SELECT ST_Envelope(boundary) as env \
                        FROM ship_regions \
                        WHERE label = ANY (%s)) as sub; \
                  """
            with self.conn.cursor() as cur:
                cur.execute(sql, (region_labels,))
                rows = cur.fetchall()
                # Convert RealDictRows/Tuples to plain lists
                return [[float(r['st_ymin']), float(r['st_xmin']),
                         float(r['st_ymax']), float(r['st_xmax'])] for r in rows]

        # Fallback: Global World Bounding Box
        return [[-90.0, -180.0, 90.0, 180.0]]

    def is_in_region(self, lat, lon, region_label):
        """Quick boolean check if a point is inside a specific region."""
        sql = """
              SELECT 1 \
              FROM ship_regions
              WHERE label = %s
                AND ST_Contains(boundary, ST_SetSRID(ST_MakePoint(%s, %s), 4326)); \
              """
        with self.conn.cursor() as cur:
            cur.execute(sql, (region_label, lon, lat))
            return cur.fetchone() is not None

    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()