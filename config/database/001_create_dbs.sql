-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Core ship data (Identity + Real-time State)
CREATE TABLE IF NOT EXISTS ships (
    mmsi VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    destination TEXT,
    vessel_type INTEGER DEFAULT 0,
    vessel_class VARCHAR(50),
    imo INTEGER,
    callsign VARCHAR(50),
    draught NUMERIC(5, 2),
    prev_draught NUMERIC(5, 2) DEFAULT 0.0,
    length INTEGER DEFAULT 0,
    beam INTEGER DEFAULT 0,
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

-- Bounding Boxes / Zones
CREATE TABLE IF NOT EXISTS map_region (
    id SERIAL PRIMARY KEY,
    label VARCHAR(100) UNIQUE,
    boundary GEOMETRY(Polygon, 4326),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Ship position history
CREATE TABLE IF NOT EXISTS ship_position (
    id BIGSERIAL PRIMARY KEY,
    mmsi VARCHAR(20) NOT NULL REFERENCES ships(mmsi) ON DELETE CASCADE,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    sog DOUBLE PRECISION DEFAULT 0.0,
    cog DOUBLE PRECISION DEFAULT 0.0,
    nav_status INTEGER DEFAULT 0,
    acquired_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lightning_strikes (
    id TEXT PRIMARY KEY,
    lat REAL,
    lon REAL,
    geom geometry(Point, 4326),
    quality TEXT,
    acquired_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS earthquakes (
    id VARCHAR(50) PRIMARY KEY, -- USGS provides a unique ID string
    mag REAL,
    depth REAL,
    place TEXT,
    eq_time TIMESTAMP WITH TIME ZONE,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326)
);

-- Master table for the active storm identity and its error cone
CREATE TABLE storms (
    sid VARCHAR(10) PRIMARY KEY,
    name VARCHAR(50),
    cone_geom GEOMETRY(Polygon, 4326),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Child table for every point along the storm track
CREATE TABLE storm_track (
    id SERIAL PRIMARY KEY,
    sid VARCHAR(10) REFERENCES storms(sid) ON DELETE CASCADE,
    record_type VARCHAR(10), -- 'PAST', 'CURRENT', 'FORECAST'
    dt TIMESTAMP WITH TIME ZONE,
    tau INTEGER,             -- Forecast hour (0 for past/current)
    lat NUMERIC,
    lon NUMERIC,
    geom GEOMETRY(Point, 4326)
);

CREATE TABLE IF NOT EXISTS satellites (
    norad_id   INTEGER PRIMARY KEY,
    name       VARCHAR(120),
    omm        JSONB NOT NULL,
    epoch      TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gfs_cache (
    gfs_date   text        NOT NULL,
    gfs_run    text        NOT NULL,
    fhour      integer     NOT NULL,
    product    text        NOT NULL,   -- 'atmos' | 'wave'
    data       bytea       NOT NULL,
    valid_time timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gfs_date, gfs_run, fhour, product)
);

-- Indices for high-performance lookups
CREATE INDEX IF NOT EXISTS idx_ships_geom ON ships USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_map_region_boundary ON map_region USING GIST(boundary);
CREATE INDEX IF NOT EXISTS idx_ships_last_update ON ships(last_position_update);
CREATE INDEX IF NOT EXISTS idx_ship_pos_mmsi ON ship_position(mmsi);
CREATE INDEX IF NOT EXISTS idx_ship_pos_time ON ship_position(acquired_at);
CREATE INDEX IF NOT EXISTS idx_ship_pos_geom ON ship_position USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_lightning_geom ON lightning_strikes USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_lightning_time ON lightning_strikes(acquired_at);
CREATE INDEX IF NOT EXISTS idx_quakes_time ON earthquakes(eq_time);
CREATE INDEX IF NOT EXISTS idx_quakes_geom ON earthquakes USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_storm_track_sid ON storm_track(sid);
CREATE INDEX IF NOT EXISTS idx_satellites_name ON satellites(name);
CREATE INDEX IF NOT EXISTS gfs_cache_run_idx  ON gfs_cache (gfs_date, gfs_run);
CREATE INDEX IF NOT EXISTS gfs_cache_seen_idx ON gfs_cache (updated_at);


-- Populate Regions
INSERT INTO map_region (label, boundary) VALUES ('NZ_Aus', ST_MakeEnvelope(63.131759, -57.173648, 190.337125, 0.239941, 4326));
INSERT INTO map_region (label, boundary) VALUES ('NZ', ST_MakeEnvelope(153.019076, -48.473543, 188.534969, -31.786772, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Suez_Canal', ST_MakeEnvelope(27.665706, 21.859824, 40.572526, 33.179878, 4326));
INSERT INTO map_region (label, boundary) VALUES ('English_Channel', ST_MakeEnvelope(-13.134662, 48.654641, 9.564140, 59.612725, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Singapore', ST_MakeEnvelope(93.568655, -7.149559, 118.816790, 10.193142, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Strait_of_Hormuz', ST_MakeEnvelope(46.049941, 19.082662, 65.784619, 30.797730, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Saudi_Arabia', ST_MakeEnvelope(18.167952, 2.294974, 71.636505, 34.836029, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Mediterranean', ST_MakeEnvelope(-24.658106, 19.590094, 40.609030, 47.955593, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Panama_Canal', ST_MakeEnvelope(-115.741454, -8.257072, -56.267345, 30.935109, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Ukraine', ST_MakeEnvelope(17.955828, 38.687680, 47.497370, 52.332015, 4326));
INSERT INTO map_region (label, boundary) VALUES ('Europe', ST_MakeEnvelope(-29.270429, 29.877027, 48.612485, 61.223772, 4326));
INSERT INTO map_region (label, boundary) VALUES ('France', ST_MakeEnvelope(-7.346384, 42.490591, 10.854976, 51.487329, 4326));
