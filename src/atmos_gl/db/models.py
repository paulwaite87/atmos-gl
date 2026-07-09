from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    REAL,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


AisVesselTier = Enum("A", "B", name="ais_vessel_tier")


class Ship(Base):
    __tablename__ = "ships"
    __table_args__ = (
        Index("idx_ships_geom", "geom", postgresql_using="gist"),
        Index("idx_ships_last_update", "last_position_update"),
    )

    mmsi: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    vessel_type: Mapped[int | None] = mapped_column(Integer)
    imo: Mapped[int | None] = mapped_column(Integer)
    callsign: Mapped[str | None] = mapped_column(String(50))
    draught: Mapped[float | None] = mapped_column(Numeric(5, 2))
    prev_draught: Mapped[float | None] = mapped_column(Numeric(5, 2), default=0.0)
    length: Mapped[int | None] = mapped_column(Integer)
    beam: Mapped[int | None] = mapped_column(Integer)
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    nav_status: Mapped[int | None] = mapped_column(Integer, default=0)
    sog: Mapped[float | None] = mapped_column(default=0.0)
    cog: Mapped[float | None] = mapped_column(default=0.0)
    last_position_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    destination: Mapped[str | None] = mapped_column(Text)
    vessel_class: Mapped[str | None] = mapped_column(String(50))
    ais_tier: Mapped[str] = mapped_column(AisVesselTier, nullable=False, default="A")


class ShipPosition(Base):
    __tablename__ = "ship_position"
    __table_args__ = (
        Index("idx_ship_pos_geom", "geom", postgresql_using="gist"),
        Index("idx_ship_pos_mmsi", "mmsi"),
        Index("idx_ship_pos_time", "acquired_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(
        String(20), ForeignKey("ships.mmsi", ondelete="CASCADE"), nullable=False
    )
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    sog: Mapped[float | None] = mapped_column(default=0.0)
    cog: Mapped[float | None] = mapped_column(default=0.0)
    nav_status: Mapped[int | None] = mapped_column(Integer, default=0)
    acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MapRegion(Base):
    __tablename__ = "map_region"
    # Index/sequence/constraint names below are stale ("ship_regions_*") from a table
    # rename that never touched them — kept as-is to match the live schema exactly.
    __table_args__ = (Index("idx_ship_regions_boundary", "boundary", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str | None] = mapped_column(String(100), unique=True)
    boundary: Mapped[str | None] = mapped_column(
        Geometry("POLYGON", srid=4326, spatial_index=False)
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )


class LightningStrike(Base):
    __tablename__ = "lightning_strikes"
    __table_args__ = (
        Index("idx_lightning_geom", "geom", postgresql_using="gist"),
        Index("idx_lightning_time", "acquired_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    lat: Mapped[float | None] = mapped_column(REAL)
    lon: Mapped[float | None] = mapped_column(REAL)
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    quality: Mapped[str | None] = mapped_column(Text)
    acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Earthquake(Base):
    __tablename__ = "earthquakes"
    __table_args__ = (
        Index("idx_quakes_geom", "geom", postgresql_using="gist"),
        Index("idx_quakes_time", "eq_time"),
    )

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    mag: Mapped[float | None] = mapped_column(REAL)
    depth: Mapped[float | None] = mapped_column(REAL)
    place: Mapped[str | None] = mapped_column(Text)
    eq_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))


class Volcano(Base):
    """No tracked CREATE TABLE existed anywhere in the repo before this model;
    schema below was reconstructed via live introspection (\\d volcanoes) — see
    the "Migrate VolcanoRepo to SQLAlchemy" ticket."""

    __tablename__ = "volcanoes"
    __table_args__ = (
        Index("idx_volcano_filters", "vei", "significant", "erupt_date_code"),
        Index("idx_volcano_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    vei: Mapped[int | None] = mapped_column(Integer)
    significant: Mapped[bool | None] = mapped_column(Boolean)
    erupt_date_code: Mapped[str | None] = mapped_column(String(10))
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))


class Storm(Base):
    __tablename__ = "storms"

    sid: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(50))
    cone_geom: Mapped[str | None] = mapped_column(
        Geometry("POLYGON", srid=4326, spatial_index=False)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StormTrack(Base):
    __tablename__ = "storm_track"
    __table_args__ = (Index("idx_storm_track_sid", "sid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sid: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("storms.sid", ondelete="CASCADE")
    )
    record_type: Mapped[str | None] = mapped_column(String(10))
    dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tau: Mapped[int | None] = mapped_column(Integer)
    lat: Mapped[float | None] = mapped_column(Numeric)
    lon: Mapped[float | None] = mapped_column(Numeric)
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))


class Satellite(Base):
    __tablename__ = "satellites"
    __table_args__ = (Index("idx_satellites_name", "name"),)

    norad_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120))
    omm: Mapped[dict] = mapped_column(JSONB, nullable=False)
    epoch: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FieldCatalog(Base):
    __tablename__ = "field_catalog"
    __table_args__ = (
        Index("idx_field_catalog_product", "product"),
        Index("idx_field_catalog_updated", text("updated_at DESC")),
    )

    run_date: Mapped[date] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(2), primary_key=True)
    fhour: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), primary_key=True)
    nlat: Mapped[int | None] = mapped_column(Integer)
    nlon: Mapped[int | None] = mapped_column(Integer)
    valid_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    storage_uri: Mapped[str | None] = mapped_column(String(256))


class BackfillRequest(Base):
    __tablename__ = "backfill_requests"
    __table_args__ = (Index("idx_backfill_status", "status"),)

    run_date: Mapped[date] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(2), primary_key=True)
    fhour: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessStatus(Base):
    __tablename__ = "process_status"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # "idle" | "running" | "success" | "failed" -- set by record_process_start()/
    # record_process_run() (db/process_status_adapter.py). started_at is cleared back
    # to NULL on completion (success or failure); it's only meaningful while status is
    # "running".
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Marker(Base):
    __tablename__ = "markers"
    __table_args__ = (Index("idx_markers_kind_priority", "kind", "priority"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="place")
    country: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int | None] = mapped_column(Integer)
    pop: Mapped[int | None] = mapped_column(BigInteger)
    capital: Mapped[bool | None] = mapped_column(Boolean)
    color: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(nullable=False)
    lon: Mapped[float] = mapped_column(nullable=False)
    geom: Mapped[str | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    wx_temp_c: Mapped[float | None] = mapped_column(REAL)
    wx_humidity_pct: Mapped[float | None] = mapped_column(REAL)
    wx_wind_ms: Mapped[float | None] = mapped_column(REAL)
    wx_wind_dir_deg: Mapped[float | None] = mapped_column(REAL)
    wx_valid_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wx_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
