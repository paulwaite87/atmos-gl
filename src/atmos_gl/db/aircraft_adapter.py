import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, cast, delete, exists, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import Aircraft, AircraftInterest, AircraftTrack, FlightRoute

logger = logging.getLogger(__name__)

# get_active_flight_positions() overfetches this many rows per requested distinct
# callsign, then dedupes (first-seen-wins, already priority-ordered) in Python --
# simpler than composing DISTINCT ON with a viewport-priority ORDER BY in one
# SQLAlchemy statement, since multiple simultaneous aircraft sharing one live
# callsign is rare enough not to warrant that complexity.
FLIGHT_POSITIONS_OVERFETCH_FACTOR = 3


def _normalize_sighting(record: dict) -> dict | None:
    """A raw adsb.lol 'ac' list entry -> our column shape, or None if it carries no
    usable hex. Unlike AIS (Ship's ShipStaticData/PositionReport split), adsb.lol hands
    back every field in one shot, so there's no separate static/position merge to do --
    just one normalization per sighting."""
    hex_id = str(record.get("hex") or "").strip().lower()
    if not hex_id:
        return None

    raw_alt = record.get("alt_baro")
    on_ground = raw_alt == "ground"
    alt_baro_ft = float(raw_alt) if isinstance(raw_alt, (int, float)) else None

    flight = (record.get("flight") or "").strip() or None

    return {
        "hex": hex_id,
        "registration": record.get("r"),
        "aircraft_type": record.get("t"),
        "category": record.get("category"),
        "flight": flight,
        "lat": record.get("lat"),
        "lon": record.get("lon"),
        "alt_baro_ft": alt_baro_ft,
        "on_ground": on_ground,
        "gs": record.get("gs"),
        "track": record.get("track"),
        "baro_rate": record.get("baro_rate"),
        "nav_altitude_mcp": record.get("nav_altitude_mcp"),
        "squawk": record.get("squawk"),
    }


class AircraftAdapter:
    """Real adapter for aircraft/aircraft_track/aircraft_interest, backed by
    SQLAlchemy (issue #215's collector/schema half)."""

    def record_sighting(self, record: dict, *, now: datetime | None = None) -> bool:
        """Upserts one adsb.lol sighting into the aircraft master row (current
        state) and appends one aircraft_track history row -- the aircraft
        equivalent of ShipAdapter.update_ship_position_data, collapsed to a single
        call since adsb.lol has no static/position message split to merge. Returns
        False (no-op) for a record with no usable hex."""
        row = _normalize_sighting(record)
        if row is None:
            return False
        now = now or datetime.now(timezone.utc)

        point = None
        if row["lat"] is not None and row["lon"] is not None:
            point = func.ST_SetSRID(func.ST_MakePoint(row["lon"], row["lat"]), 4326)

        aircraft_stmt = pg_insert(Aircraft).values(
            hex=row["hex"],
            registration=row["registration"],
            aircraft_type=row["aircraft_type"],
            category=row["category"],
            flight=row["flight"],
            lat=row["lat"],
            lon=row["lon"],
            geom=point,
            alt_baro_ft=row["alt_baro_ft"],
            on_ground=row["on_ground"],
            gs=row["gs"],
            track=row["track"],
            baro_rate=row["baro_rate"],
            nav_altitude_mcp=row["nav_altitude_mcp"],
            squawk=row["squawk"],
            last_seen=now,
            last_position_update=now,
        )
        aircraft_stmt = aircraft_stmt.on_conflict_do_update(
            index_elements=[Aircraft.hex],
            set_={
                "registration": func.coalesce(aircraft_stmt.excluded.registration, Aircraft.registration),
                "aircraft_type": func.coalesce(aircraft_stmt.excluded.aircraft_type, Aircraft.aircraft_type),
                "category": func.coalesce(aircraft_stmt.excluded.category, Aircraft.category),
                "flight": func.coalesce(aircraft_stmt.excluded.flight, Aircraft.flight),
                "lat": aircraft_stmt.excluded.lat,
                "lon": aircraft_stmt.excluded.lon,
                "geom": aircraft_stmt.excluded.geom,
                "alt_baro_ft": aircraft_stmt.excluded.alt_baro_ft,
                "on_ground": aircraft_stmt.excluded.on_ground,
                "gs": aircraft_stmt.excluded.gs,
                "track": aircraft_stmt.excluded.track,
                "baro_rate": aircraft_stmt.excluded.baro_rate,
                "nav_altitude_mcp": aircraft_stmt.excluded.nav_altitude_mcp,
                "squawk": aircraft_stmt.excluded.squawk,
                "last_seen": aircraft_stmt.excluded.last_seen,
                "last_position_update": aircraft_stmt.excluded.last_position_update,
            },
        )

        track_stmt = pg_insert(AircraftTrack).values(
            hex=row["hex"],
            lat=row["lat"],
            lon=row["lon"],
            geom=point,
            alt_baro_ft=row["alt_baro_ft"],
            on_ground=row["on_ground"],
            gs=row["gs"],
            track=row["track"],
            baro_rate=row["baro_rate"],
            category=row["category"],
            squawk=row["squawk"],
            acquired_at=now,
        )

        try:
            with Session() as session:
                session.execute(aircraft_stmt)
                session.execute(track_stmt)
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Database error recording sighting for {row['hex']}: {e}")
            return False

    def record_sightings(self, records: list[dict], *, now: datetime | None = None) -> int:
        """record_sighting() for every record in one batch; returns the count actually
        recorded (records with no usable hex are silently skipped, same as the single
        call). now is resolved once up front so every row in the batch shares one
        timestamp, rather than drifting across however long the loop takes."""
        now = now or datetime.now(timezone.utc)
        return sum(1 for record in records if self.record_sighting(record, now=now))

    def get_current_aircraft_total(self) -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(Aircraft)) or 0

    def get_fleet_as_geojson(self) -> str:
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(Aircraft.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "hex",
                Aircraft.hex,
                "registration",
                func.coalesce(Aircraft.registration, "Unknown"),
                "aircraft_type",
                func.coalesce(Aircraft.aircraft_type, "Unknown"),
                "category",
                Aircraft.category,
                "flight",
                func.coalesce(Aircraft.flight, ""),
                "alt_baro_ft",
                Aircraft.alt_baro_ft,
                "on_ground",
                func.coalesce(Aircraft.on_ground, False),
                "gs",
                func.coalesce(Aircraft.gs, 0.0),
                "track",
                func.coalesce(Aircraft.track, 0.0),
                "baro_rate",
                Aircraft.baro_rate,
                "nav_altitude_mcp",
                Aircraft.nav_altitude_mcp,
                "squawk",
                Aircraft.squawk,
                "last_seen",
                func.to_jsonb(Aircraft.last_seen),
                # Route enrichment (issue #215's route-lookup follow-on) -- joined in
                # directly rather than a separate per-hover fetch (Q6: the popup
                # that would need it is already open, and it's a cheap join). NULL
                # for both when flight_route has no row yet for this callsign, or
                # the callsign has no known route at all -- popupHtml hides the
                # whole "Route" line in either case rather than showing a partial one.
                "route_stops",
                FlightRoute.stops,
                "route_plausible",
                FlightRoute.plausible,
            ),
        )
        collection = as_feature_collection(feature)
        stmt = (
            select(cast(collection, SqlText))
            .select_from(Aircraft)
            .outerjoin(FlightRoute, FlightRoute.flight == Aircraft.flight)
            .where(Aircraft.geom.isnot(None))
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building native aircraft GeoJSON layer: {e}")
            return EMPTY_FEATURE_COLLECTION

    def get_aircraft_track(self, hex_id, limit=100):
        """Historical positions for one aircraft, newest first -- the aircraft
        equivalent of ShipAdapter.get_ship_track."""
        if not hex_id:
            return []
        try:
            with Session() as session:
                rows = session.execute(
                    select(AircraftTrack.lat, AircraftTrack.lon)
                    .where(AircraftTrack.hex == str(hex_id).lower())
                    .order_by(AircraftTrack.acquired_at.desc())
                    .limit(limit)
                ).all()
                return [{"lat": r.lat, "lon": r.lon} for r in rows]
        except Exception as e:
            logger.error(f"Error fetching track for aircraft {hex_id}: {e}")
            return []

    def prune_aircraft_tracks(self, expiry_hours: float) -> int:
        """Removes aircraft_track history older than expiry_hours -- the aircraft
        equivalent of ShipAdapter.prune_vessel_tracks, called only by Housekeeper,
        never by AircraftCollector itself."""
        if not expiry_hours or expiry_hours <= 0:
            return 0
        try:
            with Session() as session:
                result = session.execute(
                    delete(AircraftTrack).where(
                        AircraftTrack.acquired_at < func.now() - timedelta(hours=expiry_hours)
                    )
                )
                session.commit()
                deleted_rows = result.rowcount
                if deleted_rows > 0:
                    logger.info(f"Pruned {deleted_rows} old aircraft track record(s).")
                return deleted_rows
        except Exception as e:
            logger.error(f"Error pruning aircraft tracks: {e}")
            return 0

    def record_interest(self, viewer_id: str, west: float, south: float, east: float, north: float) -> None:
        """Upserts one active viewer's current viewport -- written by the REST route
        (not yet built; issue #215's remaining half) as a side effect of the same
        request that reads current aircraft, read each cycle by AircraftCollector to
        prioritize whichever areas have an active viewer."""
        stmt = pg_insert(AircraftInterest).values(
            viewer_id=viewer_id, west=west, south=south, east=east, north=north,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AircraftInterest.viewer_id],
            set_={
                "west": stmt.excluded.west,
                "south": stmt.excluded.south,
                "east": stmt.excluded.east,
                "north": stmt.excluded.north,
                "last_seen_at": func.now(),
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Database error recording viewer interest for {viewer_id}: {e}")

    def get_active_interest(self, max_age_s: float) -> list[tuple[float, float, float, float]]:
        """Every currently-active viewer's viewport (west, south, east, north),
        excluding rows not refreshed within max_age_s -- a stale row means that
        viewer's session has gone away, same as a disconnected RegionManager
        subscriber."""
        try:
            with Session() as session:
                rows = session.execute(
                    select(
                        AircraftInterest.west,
                        AircraftInterest.south,
                        AircraftInterest.east,
                        AircraftInterest.north,
                    ).where(
                        AircraftInterest.last_seen_at
                        >= func.now() - timedelta(seconds=max_age_s)
                    )
                ).all()
                return [(r.west, r.south, r.east, r.north) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching active aircraft interest: {e}")
            return []

    def get_active_flight_positions(self, interest_max_age_s: float, limit: int) -> list[dict]:
        """Up to `limit` DISTINCT currently-known callsigns (Aircraft.flight), each
        with one representative (lat, lng) -- the priority feed AircraftCollector's
        route-enrichment tick reads from (issue #215's route-lookup follow-on), ordered
        so callsigns currently within an active viewer's viewport (same max-age
        semantics as get_active_interest) sort first, then by most-recently-seen.

        Deliberately no knowledge of flight_route here -- this is "what callsigns
        exist and where are they, prioritized", not "which of them need a lookup"
        (see FlightRouteAdapter.filter_stale, which narrows this list down without
        needing to know anything about Aircraft/AircraftInterest itself)."""
        in_viewport = exists(
            select(AircraftInterest.viewer_id).where(
                AircraftInterest.last_seen_at >= func.now() - timedelta(seconds=interest_max_age_s),
                Aircraft.lon >= AircraftInterest.west,
                Aircraft.lon <= AircraftInterest.east,
                Aircraft.lat >= AircraftInterest.south,
                Aircraft.lat <= AircraftInterest.north,
            )
        ).correlate(Aircraft)

        stmt = (
            select(Aircraft.flight, Aircraft.lat, Aircraft.lon)
            .where(Aircraft.flight.isnot(None), Aircraft.flight != "")
            .order_by(case((in_viewport, 0), else_=1), Aircraft.last_seen.desc())
            .limit(limit * FLIGHT_POSITIONS_OVERFETCH_FACTOR)
        )
        try:
            with Session() as session:
                rows = session.execute(stmt).all()
        except Exception as e:
            logger.error(f"Error fetching active flight positions: {e}")
            return []

        seen: dict[str, dict] = {}
        for r in rows:
            if r.flight not in seen:
                seen[r.flight] = {"callsign": r.flight, "lat": r.lat or 0.0, "lng": r.lon or 0.0}
                if len(seen) >= limit:
                    break
        return list(seen.values())


class FakeAircraftAdapter:
    """In-memory fake for aircraft/aircraft_track/aircraft_interest, matching
    AircraftAdapter's method contracts."""

    def __init__(self):
        self._aircraft: dict[str, dict] = {}
        self._tracks: list[dict] = []
        self._interest: dict[str, dict] = {}

    def record_sighting(self, record: dict, *, now: datetime | None = None) -> bool:
        row = _normalize_sighting(record)
        if row is None:
            return False
        now = now or datetime.now(timezone.utc)

        existing = self._aircraft.get(row["hex"], {})
        merged = dict(existing)
        merged.update(
            {
                "hex": row["hex"],
                "registration": row["registration"] or existing.get("registration"),
                "aircraft_type": row["aircraft_type"] or existing.get("aircraft_type"),
                "category": row["category"] or existing.get("category"),
                "flight": row["flight"] or existing.get("flight"),
                "lat": row["lat"],
                "lon": row["lon"],
                "geom": (row["lon"], row["lat"]) if row["lat"] is not None and row["lon"] is not None else None,
                "alt_baro_ft": row["alt_baro_ft"],
                "on_ground": row["on_ground"],
                "gs": row["gs"],
                "track": row["track"],
                "baro_rate": row["baro_rate"],
                "nav_altitude_mcp": row["nav_altitude_mcp"],
                "squawk": row["squawk"],
                "last_seen": now,
                "last_position_update": now,
            }
        )
        self._aircraft[row["hex"]] = merged

        self._tracks.append(
            {
                "hex": row["hex"],
                "lat": row["lat"],
                "lon": row["lon"],
                "alt_baro_ft": row["alt_baro_ft"],
                "on_ground": row["on_ground"],
                "gs": row["gs"],
                "track": row["track"],
                "baro_rate": row["baro_rate"],
                "category": row["category"],
                "squawk": row["squawk"],
                "acquired_at": now,
            }
        )
        return True

    def record_sightings(self, records: list[dict], *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return sum(1 for record in records if self.record_sighting(record, now=now))

    def get_current_aircraft_total(self) -> int:
        return len(self._aircraft)

    def get_fleet_as_geojson(self) -> str:
        import json

        features = []
        for hex_id, ac in self._aircraft.items():
            if ac.get("geom") is None:
                continue
            lon, lat = ac["geom"]
            last_seen = ac.get("last_seen")
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "hex": hex_id,
                        "registration": ac.get("registration") or "Unknown",
                        "aircraft_type": ac.get("aircraft_type") or "Unknown",
                        "category": ac.get("category"),
                        "flight": ac.get("flight") or "",
                        "alt_baro_ft": ac.get("alt_baro_ft"),
                        "on_ground": bool(ac.get("on_ground")),
                        "gs": ac.get("gs") if ac.get("gs") is not None else 0.0,
                        "track": ac.get("track") if ac.get("track") is not None else 0.0,
                        "baro_rate": ac.get("baro_rate"),
                        "nav_altitude_mcp": ac.get("nav_altitude_mcp"),
                        "squawk": ac.get("squawk"),
                        "last_seen": last_seen.isoformat() if last_seen else None,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def get_aircraft_track(self, hex_id, limit=100):
        if not hex_id:
            return []
        hex_id = str(hex_id).lower()
        rows = [t for t in self._tracks if t["hex"] == hex_id]
        rows.sort(key=lambda t: t["acquired_at"], reverse=True)
        return [{"lat": t["lat"], "lon": t["lon"]} for t in rows[:limit]]

    def prune_aircraft_tracks(self, expiry_hours: float) -> int:
        if not expiry_hours or expiry_hours <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        before = len(self._tracks)
        self._tracks = [t for t in self._tracks if t["acquired_at"] >= cutoff]
        return before - len(self._tracks)

    def record_interest(self, viewer_id: str, west: float, south: float, east: float, north: float) -> None:
        self._interest[viewer_id] = {
            "west": west, "south": south, "east": east, "north": north,
            "last_seen_at": datetime.now(timezone.utc),
        }

    def get_active_interest(self, max_age_s: float) -> list[tuple[float, float, float, float]]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_s)
        return [
            (row["west"], row["south"], row["east"], row["north"])
            for row in self._interest.values()
            if row["last_seen_at"] >= cutoff
        ]

    def get_active_flight_positions(self, interest_max_age_s: float, limit: int) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=interest_max_age_s)
        active_viewports = [row for row in self._interest.values() if row["last_seen_at"] >= cutoff]

        def in_viewport(lat, lon):
            if lat is None or lon is None:
                return False
            return any(
                v["west"] <= lon <= v["east"] and v["south"] <= lat <= v["north"]
                for v in active_viewports
            )

        candidates = [ac for ac in self._aircraft.values() if ac.get("flight")]
        candidates.sort(
            key=lambda ac: (
                0 if in_viewport(ac.get("lat"), ac.get("lon")) else 1,
                -ac["last_seen"].timestamp(),
            )
        )

        seen: dict[str, dict] = {}
        for ac in candidates:
            flight = ac["flight"]
            if flight not in seen:
                seen[flight] = {"callsign": flight, "lat": ac.get("lat") or 0.0, "lng": ac.get("lon") or 0.0}
                if len(seen) >= limit:
                    break
        return list(seen.values())
