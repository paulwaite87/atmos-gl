import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.models import FlightRoute

logger = logging.getLogger(__name__)


class FlightRouteAdapter:
    """Real adapter for flight_route, backed by SQLAlchemy (issue #215's route-lookup
    follow-on). Keyed on callsign (Aircraft.flight), not hex -- see FlightRoute's own
    docstring for why. Deliberately knows nothing about Aircraft/AircraftInterest;
    see AircraftAdapter.get_active_flight_positions for the priority-ordered candidate
    feed this adapter's filter_stale() narrows down."""

    def filter_stale(self, callsigns: list[str], backstop_days: float) -> list[str]:
        """Given candidate callsigns (already priority-ordered by the caller), returns
        the subset missing or stale (checked_at older than backstop_days) in
        flight_route, preserving input order. A callsign with no row at all counts as
        stale (never checked) -- applies uniformly whether or not a prior lookup found
        a match, per this feature's Q3/Q4 design (one backstop for both cases)."""
        if not callsigns:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=backstop_days)
        try:
            with Session() as session:
                fresh_rows = session.execute(
                    select(FlightRoute.flight).where(
                        FlightRoute.flight.in_(callsigns),
                        FlightRoute.checked_at.isnot(None),
                        FlightRoute.checked_at >= cutoff,
                    )
                ).all()
        except Exception as e:
            logger.error(f"Error checking flight_route staleness: {e}")
            return []
        fresh = {r.flight for r in fresh_rows}
        return [cs for cs in callsigns if cs not in fresh]

    def record_routes(self, routes: dict[str, dict | None], *, now: datetime | None = None) -> None:
        """Upserts one flight_route row per callsign in `routes`. A dict value of None
        is fetch_routes()'s own confirmed no-match sentinel -- stored as NULL
        stops/plausible rather than skipped, so the same 7-day backstop (checked_at)
        governs retry timing for matches and non-matches alike."""
        if not routes:
            return
        now = now or datetime.now(timezone.utc)
        try:
            with Session() as session:
                for callsign, route in routes.items():
                    stmt = pg_insert(FlightRoute).values(
                        flight=callsign,
                        stops=route["stops"] if route else None,
                        plausible=route["plausible"] if route else None,
                        checked_at=now,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[FlightRoute.flight],
                        set_={
                            "stops": stmt.excluded.stops,
                            "plausible": stmt.excluded.plausible,
                            "checked_at": stmt.excluded.checked_at,
                        },
                    )
                    session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Database error recording flight routes: {e}")


class FakeFlightRouteAdapter:
    """In-memory fake for flight_route, matching FlightRouteAdapter's method contracts."""

    def __init__(self):
        self._routes: dict[str, dict] = {}  # flight -> {"stops":..., "plausible":..., "checked_at":...}

    def filter_stale(self, callsigns: list[str], backstop_days: float) -> list[str]:
        if not callsigns:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=backstop_days)
        stale = []
        for cs in callsigns:
            row = self._routes.get(cs)
            if row is None or row.get("checked_at") is None or row["checked_at"] < cutoff:
                stale.append(cs)
        return stale

    def record_routes(self, routes: dict[str, dict | None], *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for callsign, route in routes.items():
            self._routes[callsign] = {
                "stops": route["stops"] if route else None,
                "plausible": route["plausible"] if route else None,
                "checked_at": now,
            }
