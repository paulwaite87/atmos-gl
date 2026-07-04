import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, cast, delete, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from worldmap.db.engine import Session
from worldmap.db.models import Ship, ShipPosition
from worldmap.lib.shipping import get_vessel_class_from_type

logger = logging.getLogger(__name__)


def _parse_ais_timestamp(raw_time_str):
    if not raw_time_str:
        return datetime.now()
    timestamp = raw_time_str.replace(" UTC", "")
    main_part, tz_part = timestamp.split(" +")
    cleaned_timestamp = f"{main_part[:26]} +{tz_part}"
    return datetime.strptime(cleaned_timestamp, "%Y-%m-%d %H:%M:%S.%f %z")


class ShipAdapter:
    """Real adapter for ships/ship_position, backed by SQLAlchemy."""

    def update_ship_static_data(self, mmsi, metadata, body, ais_tier="A"):
        """Processes ShipStaticData and UPSERTs into the ships table."""
        mmsi = str(mmsi)
        name = metadata.get("ShipName", "Unknown").strip()
        destination = body.get("Destination", "").strip()
        v_type = body.get("Type", 0)
        v_class = get_vessel_class_from_type(v_type)
        imo = body.get("ImoNumber", 0)
        callsign = body.get("CallSign", "").strip()
        draught = float(body.get("MaximumStaticDraught", 0.0))

        dim = body.get("Dimension", {})
        length = int(dim.get("A", 0)) + int(dim.get("B", 0))
        beam = int(dim.get("C", 0)) + int(dim.get("D", 0))

        stmt = pg_insert(Ship).values(
            mmsi=mmsi,
            name=name,
            destination=destination,
            vessel_type=v_type,
            vessel_class=v_class,
            imo=imo,
            callsign=callsign,
            draught=draught,
            prev_draught=0.0,
            length=length,
            beam=beam,
            ais_tier=ais_tier,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Ship.mmsi],
            set_={
                "prev_draught": case(
                    (
                        and_(Ship.draught != stmt.excluded.draught, stmt.excluded.draught > 0),
                        Ship.draught,
                    ),
                    else_=Ship.prev_draught,
                ),
                "name": stmt.excluded.name,
                "destination": stmt.excluded.destination,
                "vessel_type": stmt.excluded.vessel_type,
                "vessel_class": stmt.excluded.vessel_class,
                "imo": stmt.excluded.imo,
                "callsign": stmt.excluded.callsign,
                "draught": stmt.excluded.draught,
                "length": stmt.excluded.length,
                "beam": stmt.excluded.beam,
                "ais_tier": stmt.excluded.ais_tier,
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def update_ship_position_data(self, mmsi, metadata, body, ais_tier="A"):
        mmsi = str(mmsi)
        vessel_type = body.get("Type", 0)
        lat = body.get("Latitude", metadata.get("Latitude"))
        lon = body.get("Longitude", metadata.get("Longitude"))
        nav_status = body.get("NavigationalStatus", 0)
        cog = body.get("Cog", 0.0)
        sog = body.get("Sog", 0.0)
        name = metadata.get("ShipName", "Unknown").strip()
        msg_datetime = _parse_ais_timestamp(metadata.get("time_utc", ""))

        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)

        ship_stmt = pg_insert(Ship).values(
            mmsi=mmsi,
            name=name,
            vessel_type=vessel_type,
            ais_tier=ais_tier,
            lat=lat,
            lon=lon,
            geom=point,
            nav_status=nav_status,
            cog=cog,
            sog=sog,
            last_position_update=msg_datetime,
        )
        ship_stmt = ship_stmt.on_conflict_do_update(
            index_elements=[Ship.mmsi],
            set_={
                "name": case(
                    (
                        and_(
                            ship_stmt.excluded.name.isnot(None),
                            ship_stmt.excluded.name.notin_(["", "Unknown"]),
                        ),
                        ship_stmt.excluded.name,
                    ),
                    else_=Ship.name,
                ),
                "vessel_type": case(
                    (Ship.vessel_type != 0, Ship.vessel_type),
                    else_=ship_stmt.excluded.vessel_type,
                ),
                "ais_tier": ship_stmt.excluded.ais_tier,
                "lat": ship_stmt.excluded.lat,
                "lon": ship_stmt.excluded.lon,
                "geom": ship_stmt.excluded.geom,
                "nav_status": ship_stmt.excluded.nav_status,
                "cog": ship_stmt.excluded.cog,
                "sog": ship_stmt.excluded.sog,
                "last_position_update": ship_stmt.excluded.last_position_update,
            },
        )

        position_stmt = pg_insert(ShipPosition).values(
            mmsi=mmsi,
            lat=lat,
            lon=lon,
            geom=point,
            sog=sog,
            cog=cog,
            nav_status=nav_status,
            acquired_at=msg_datetime,
        )

        try:
            with Session() as session:
                session.execute(ship_stmt)
                session.execute(position_stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Database error updating position for {mmsi}: {e}")

    def get_current_ship_total(self):
        """Returns the total number of ships currently in the database."""
        with Session() as session:
            return session.scalar(select(func.count()).select_from(Ship)) or 0

    def get_fleet_as_geojson(self):
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Ship.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "mmsi",
                Ship.mmsi,
                "name",
                Ship.name,
                "heading",
                func.coalesce(Ship.cog, 0.0),
                "speed",
                func.coalesce(Ship.sog, 0.0),
                "length",
                func.coalesce(Ship.length, 0),
                "beam",
                func.coalesce(Ship.beam, 0),
                "vessel_type",
                func.coalesce(Ship.vessel_type, 0),
                "destination",
                func.coalesce(Ship.destination, "Unknown"),
                "vessel_class",
                func.coalesce(Ship.vessel_class, "Unknown"),
                "imo",
                func.coalesce(Ship.imo, 0),
                "callsign",
                func.coalesce(Ship.callsign, "N/A"),
                "draught",
                func.coalesce(Ship.draught, 0.0),
                "last_position_update",
                func.to_jsonb(Ship.last_position_update),
            ),
        )
        collection = func.jsonb_build_object(
            "type",
            "FeatureCollection",
            "features",
            func.coalesce(func.jsonb_agg(feature), text("'[]'::jsonb")),
        )
        stmt = select(cast(collection, SqlText)).where(Ship.geom.isnot(None))
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else '{"type":"FeatureCollection","features":[]}'
        except Exception as e:
            logger.error(f"Error building native fleet GeoJSON layer: {e}")
            return '{"type":"FeatureCollection","features":[]}'

    def get_ship_track(self, mmsi, limit=100):
        """Historical positions for a specific ship, newest first."""
        if not mmsi:
            return []
        try:
            with Session() as session:
                rows = session.execute(
                    select(ShipPosition.lat, ShipPosition.lon)
                    .where(ShipPosition.mmsi == str(mmsi))
                    .order_by(ShipPosition.acquired_at.desc())
                    .limit(limit)
                ).all()
                return [{"lat": r.lat, "lon": r.lon} for r in rows]
        except Exception as e:
            logger.error(f"Error fetching track for MMSI {mmsi}: {e}")
            return []

    def prune_vessel_tracks(self, expiry_days):
        """Removes position history older than the specified number of days."""
        if not expiry_days or expiry_days <= 0:
            return 0
        try:
            with Session() as session:
                result = session.execute(
                    delete(ShipPosition).where(
                        ShipPosition.acquired_at < func.now() - timedelta(days=expiry_days)
                    )
                )
                session.commit()
                deleted_rows = result.rowcount
                if deleted_rows > 0:
                    logger.info(f"Pruned {deleted_rows} old position records.")
                return deleted_rows
        except Exception as e:
            logger.error(f"Error pruning vessel tracks: {e}")
            return 0


class FakeShipAdapter:
    """In-memory fake for ships/ship_position, matching ShipAdapter's method contracts."""

    def __init__(self):
        self._ships: dict[str, dict] = {}
        self._positions: list[dict] = []

    def _blank_ship(self, mmsi):
        return {
            "mmsi": mmsi,
            "name": None,
            "vessel_type": None,
            "imo": None,
            "callsign": None,
            "draught": None,
            "prev_draught": 0.0,
            "length": None,
            "beam": None,
            "lat": None,
            "lon": None,
            "nav_status": None,
            "sog": None,
            "cog": None,
            "last_position_update": None,
            "geom": None,
            "destination": None,
            "vessel_class": None,
            "ais_tier": "A",
        }

    def update_ship_static_data(self, mmsi, metadata, body, ais_tier="A"):
        mmsi = str(mmsi)
        name = metadata.get("ShipName", "Unknown").strip()
        destination = body.get("Destination", "").strip()
        v_type = body.get("Type", 0)
        v_class = get_vessel_class_from_type(v_type)
        imo = body.get("ImoNumber", 0)
        callsign = body.get("CallSign", "").strip()
        draught = float(body.get("MaximumStaticDraught", 0.0))
        dim = body.get("Dimension", {})
        length = int(dim.get("A", 0)) + int(dim.get("B", 0))
        beam = int(dim.get("C", 0)) + int(dim.get("D", 0))

        existing = self._ships.get(mmsi)
        if existing is None:
            prev_draught = 0.0
            row = self._blank_ship(mmsi)
        else:
            if existing["draught"] != draught and draught > 0:
                prev_draught = existing["draught"]
            else:
                prev_draught = existing["prev_draught"]
            row = dict(existing)

        row.update(
            {
                "name": name,
                "destination": destination,
                "vessel_type": v_type,
                "vessel_class": v_class,
                "imo": imo,
                "callsign": callsign,
                "draught": draught,
                "prev_draught": prev_draught,
                "length": length,
                "beam": beam,
                "ais_tier": ais_tier,
            }
        )
        self._ships[mmsi] = row

    def update_ship_position_data(self, mmsi, metadata, body, ais_tier="A"):
        mmsi = str(mmsi)
        vessel_type = body.get("Type", 0)
        lat = body.get("Latitude", metadata.get("Latitude"))
        lon = body.get("Longitude", metadata.get("Longitude"))
        nav_status = body.get("NavigationalStatus", 0)
        cog = body.get("Cog", 0.0)
        sog = body.get("Sog", 0.0)
        name = metadata.get("ShipName", "Unknown").strip()
        msg_datetime = _parse_ais_timestamp(metadata.get("time_utc", ""))

        row = dict(self._ships.get(mmsi) or self._blank_ship(mmsi))
        if name and name not in ("", "Unknown"):
            new_name = name
        else:
            new_name = row.get("name")

        existing_vtype = row.get("vessel_type") or 0
        new_vtype = existing_vtype if existing_vtype != 0 else vessel_type

        row.update(
            {
                "name": new_name,
                "vessel_type": new_vtype,
                "ais_tier": ais_tier,
                "lat": lat,
                "lon": lon,
                "geom": (lon, lat),
                "nav_status": nav_status,
                "cog": cog,
                "sog": sog,
                "last_position_update": msg_datetime,
            }
        )
        self._ships[mmsi] = row

        self._positions.append(
            {
                "mmsi": mmsi,
                "lat": lat,
                "lon": lon,
                "geom": (lon, lat),
                "sog": sog,
                "cog": cog,
                "nav_status": nav_status,
                "acquired_at": msg_datetime,
            }
        )

    def get_current_ship_total(self):
        return len(self._ships)

    def get_fleet_as_geojson(self):
        import json

        features = []
        for mmsi, ship in self._ships.items():
            if ship.get("geom") is None:
                continue
            lon, lat = ship["geom"]
            last_position_update = ship.get("last_position_update")
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "mmsi": mmsi,
                        "name": ship.get("name"),
                        "heading": ship.get("cog") if ship.get("cog") is not None else 0.0,
                        "speed": ship.get("sog") if ship.get("sog") is not None else 0.0,
                        "length": ship.get("length") if ship.get("length") is not None else 0,
                        "beam": ship.get("beam") if ship.get("beam") is not None else 0,
                        "vessel_type": ship.get("vessel_type")
                        if ship.get("vessel_type") is not None
                        else 0,
                        "destination": ship.get("destination") or "Unknown",
                        "vessel_class": ship.get("vessel_class") or "Unknown",
                        "imo": ship.get("imo") if ship.get("imo") is not None else 0,
                        "callsign": ship.get("callsign") or "N/A",
                        "draught": ship.get("draught") if ship.get("draught") is not None else 0.0,
                        "last_position_update": last_position_update.isoformat()
                        if last_position_update
                        else None,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def get_ship_track(self, mmsi, limit=100):
        if not mmsi:
            return []
        mmsi = str(mmsi)
        rows = [p for p in self._positions if p["mmsi"] == mmsi]
        rows.sort(key=lambda p: p["acquired_at"], reverse=True)
        return [{"lat": p["lat"], "lon": p["lon"]} for p in rows[:limit]]

    def prune_vessel_tracks(self, expiry_days):
        if not expiry_days or expiry_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        before = len(self._positions)
        self._positions = [p for p in self._positions if p["acquired_at"] >= cutoff]
        return before - len(self._positions)
