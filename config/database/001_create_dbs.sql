-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Core ship data (Identity + Real-time State)
CREATE TABLE IF NOT EXISTS ships (
    mmsi VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    vessel_type INTEGER,
    imo INTEGER,
    callsign VARCHAR(50),
    draught NUMERIC(5, 2),
    prev_draught NUMERIC(5, 2) DEFAULT 0.0,
    length INTEGER,
    beam INTEGER,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Real-time position and navigation data
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    nav_status INTEGER DEFAULT 0,
    sog DOUBLE PRECISION DEFAULT 0.0,
    cog DOUBLE PRECISION DEFAULT 0.0,
    last_position_update TIMESTAMP WITH TIME ZONE,
    geom GEOMETRY(Point, 4326)
);

-- 2. Bounding Boxes / Zones
CREATE TABLE IF NOT EXISTS ship_regions (
    id SERIAL PRIMARY KEY,
    label VARCHAR(100) UNIQUE,
    boundary GEOMETRY(Polygon, 4326),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Indices for high-performance lookups
CREATE INDEX IF NOT EXISTS idx_ships_geom ON ships USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_ship_regions_boundary ON ship_regions USING GIST(boundary);
CREATE INDEX IF NOT EXISTS idx_ships_last_update ON ships(last_position_update);

-- 4. Populate Regions using COPY from STDIN
-- Format: label | WKT Geometry
COPY ship_regions (label, boundary) FROM STDIN WITH (FORMAT csv, DELIMITER '|');
NZ_Aus|SRID=4326;POLYGON((110 -10, 180 -10, 180 -50, 110 -50, 110 -10))
Suez_Canal|SRID=4326;POLYGON((32.2 31.4, 32.6 31.4, 32.6 29.8, 32.2 29.8, 32.2 31.4))
English_Channel|SRID=4326;POLYGON((-6.5 51.5, 2.5 51.5, 2.5 48.5, -6.5 48.5, -6.5 51.5))
Strait_of_Hormuz|SRID=4326;POLYGON((55.5 27.5, 57.5 27.5, 57.5 25.5, 55.5 25.5, 55.5 27.5))
Gulf_of_Aden|SRID=4326;POLYGON((43.0 15.0, 52.0 15.0, 52.0 10.0, 43.0 10.0, 43.0 15.0))
Panama_Canal|SRID=4326;POLYGON((-80.0 9.5, -79.3 9.5, -79.3 8.7, -80.0 8.7, -80.0 9.5))
Strait_of_Malacca|SRID=4326;POLYGON((94.5 6.5, 105.0 6.5, 105.0 -1.5, 94.5 -1.5, 94.5 6.5))
\.
