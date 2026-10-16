import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from terrascope.db.engine import Session
from terrascope.db.models import InternetOutageEvent

logger = logging.getLogger(__name__)

_UTC_FORMAT_PY = "%Y-%m-%dT%H:%M:%SZ"
# More than this many distinct signals still reads as "all agree".
MAX_SEVERITY = 3


def aggregate_countries(rows: list[dict], now: datetime) -> dict[str, dict]:
    """Collapse event rows (one per country, signal and start) into one entry per
    country: its severity is the number of distinct signals among its events (capped at
    MAX_SEVERITY -- the corroboration rule, not IODA's raw scores), it is ongoing if any
    event is, and it lists its signals oldest first. Shared by the real and fake adapters
    so the rule cannot drift between them."""
    countries: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["start"]):
        entry = countries.setdefault(
            row["country_code"],
            {
                "name": row["country_name"],
                "severity": 0,
                "ongoing": False,
                "signals": [],
            },
        )
        end = now if row["ongoing"] else row["end"]
        entry["signals"].append(
            {
                "signal": row["signal"],
                "start": row["start"].strftime(_UTC_FORMAT_PY),
                "duration_s": int((end - row["start"]).total_seconds()),
                "ongoing": row["ongoing"],
            }
        )
        entry["ongoing"] = entry["ongoing"] or row["ongoing"]
    for entry in countries.values():
        distinct = {s["signal"] for s in entry["signals"]}
        entry["severity"] = min(len(distinct), MAX_SEVERITY)
    return countries


class InternetOutagesAdapter:
    """Real adapter for internet outage events, backed by SQLAlchemy."""

    def upsert_event(self, event: dict) -> None:
        """UPSERTs an event keyed on (country_code, signal, start)."""
        values = {
            "country_name": event["country_name"],
            "end_time": event["end"],
            "ongoing": event["ongoing"],
            "score": event["score"],
        }
        stmt = pg_insert(InternetOutageEvent).values(
            country_code=event["country_code"],
            signal=event["signal"],
            start_time=event["start"],
            **values,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                InternetOutageEvent.country_code,
                InternetOutageEvent.signal,
                InternetOutageEvent.start_time,
            ],
            set_={k: stmt.excluded[k] for k in values},
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(
                f"Error saving internet outage {event['country_code']}/{event['signal']}: {e}"
            )

    def close_unseen_ongoing(self, seen: set[tuple[str, str, datetime]]) -> int:
        """Marks every stored ongoing event that is not in `seen` (keys of (country_code,
        signal, start)) as ended, so an event IODA stopped returning does not stay ongoing
        forever. Returns the number closed."""
        try:
            with Session() as session:
                ongoing = session.execute(
                    select(
                        InternetOutageEvent.country_code,
                        InternetOutageEvent.signal,
                        InternetOutageEvent.start_time,
                    ).where(InternetOutageEvent.ongoing.is_(True))
                ).all()
                stale = [tuple(row) for row in ongoing if tuple(row) not in seen]
                for code, signal, start in stale:
                    session.execute(
                        update(InternetOutageEvent)
                        .where(
                            InternetOutageEvent.country_code == code,
                            InternetOutageEvent.signal == signal,
                            InternetOutageEvent.start_time == start,
                        )
                        .values(ongoing=False)
                    )
                session.commit()
                return len(stale)
        except Exception as e:
            logger.error(f"Error closing unseen internet outages: {e}")
            return 0

    def get_countries_as_json(self, grace_days: float = 1) -> str:
        """Countries with an ongoing event, plus ones whose events ended within
        `grace_days`, keyed by ISO country code (see aggregate_countries)."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=grace_days)
        stmt = select(InternetOutageEvent).where(
            (InternetOutageEvent.ongoing.is_(True))
            | (InternetOutageEvent.end_time >= cutoff)
        )
        try:
            with Session() as session:
                rows = [
                    {
                        "country_code": e.country_code,
                        "country_name": e.country_name,
                        "signal": e.signal,
                        "start": e.start_time,
                        "end": e.end_time,
                        "ongoing": bool(e.ongoing),
                    }
                    for e in session.scalars(stmt)
                ]
        except Exception as e:
            logger.error(f"Error building internet outages JSON: {e}")
            rows = []
        return json.dumps({"countries": aggregate_countries(rows, now)})

    def prune_expired(self, expiry_days: float) -> int:
        """Deletes events that ended more than `expiry_days` ago; ongoing events are never
        pruned. Returns the number of events deleted."""
        cutoff = func.now() - timedelta(days=expiry_days)
        with Session() as session:
            result = session.execute(
                delete(InternetOutageEvent).where(
                    InternetOutageEvent.ongoing.is_not(True),
                    InternetOutageEvent.end_time < cutoff,
                )
            )
            session.commit()
            return result.rowcount


class FakeInternetOutagesAdapter:
    """In-memory fake for internet outage events, matching InternetOutagesAdapter's
    method contracts."""

    def __init__(self):
        self._events: dict[tuple[str, str, datetime], dict] = {}

    def upsert_event(self, event: dict) -> None:
        self._events[(event["country_code"], event["signal"], event["start"])] = dict(
            event
        )

    def get_countries_as_json(self, grace_days: float = 1) -> str:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=grace_days)
        visible = [
            e for e in self._events.values() if e["ongoing"] or e["end"] >= cutoff
        ]
        return json.dumps({"countries": aggregate_countries(visible, now)})

    def close_unseen_ongoing(self, seen: set[tuple[str, str, datetime]]) -> int:
        stale = [k for k, e in self._events.items() if e["ongoing"] and k not in seen]
        for key in stale:
            self._events[key]["ongoing"] = False
        return len(stale)

    def prune_expired(self, expiry_days: float) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=expiry_days)
        expired = [
            k for k, e in self._events.items() if not e["ongoing"] and e["end"] < cutoff
        ]
        for key in expired:
            del self._events[key]
        return len(expired)
