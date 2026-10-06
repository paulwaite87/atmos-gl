import logging

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atmos_gl.db.engine import Session
from atmos_gl.db.models import CountryIndicator, CountryIndicatorValue

logger = logging.getLogger(__name__)

_METADATA_FIELDS = ("title", "unit", "short_unit", "citation", "source_last_updated", "next_update")


class CountryStatsAdapter:
    """Real adapter for Country Statistics indicators, backed by SQLAlchemy."""

    def get_source_last_updated(self, indicator_id: str) -> str | None:
        """OWID's lastUpdated as stored with the indicator; None if it isn't stored."""
        with Session() as session:
            return session.execute(
                select(CountryIndicator.source_last_updated).where(CountryIndicator.id == indicator_id)
            ).scalar()

    def save_indicator(self, indicator_id: str, metadata: dict, rows: dict) -> None:
        """Stores the indicator's metadata and replaces all its values with `rows`
        ({code: (year, value)}), in one transaction."""
        values = {k: metadata.get(k) for k in _METADATA_FIELDS}
        stmt = pg_insert(CountryIndicator).values(id=indicator_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CountryIndicator.id],
            set_={**values, "fetched_at": func.now()},
        )
        with Session() as session:
            session.execute(stmt)
            session.execute(
                delete(CountryIndicatorValue).where(CountryIndicatorValue.indicator_id == indicator_id)
            )
            if rows:
                session.execute(pg_insert(CountryIndicatorValue).values([
                    {"indicator_id": indicator_id, "country_code": code, "year": year, "value": value}
                    for code, (year, value) in rows.items()
                ]))
            session.commit()

    def get_indicator(self, indicator_id: str) -> dict | None:
        """{metadata: {...}, rows: {code: (year, value)}}; None if it isn't stored."""
        with Session() as session:
            indicator = session.get(CountryIndicator, indicator_id)
            if indicator is None:
                return None
            values = session.execute(
                select(CountryIndicatorValue).where(CountryIndicatorValue.indicator_id == indicator_id)
            ).scalars()
            return {
                "metadata": {k: getattr(indicator, k) for k in _METADATA_FIELDS},
                "rows": {v.country_code: (v.year, v.value) for v in values},
            }


class FakeCountryStatsAdapter:
    """In-memory fake matching CountryStatsAdapter's method contracts."""

    def __init__(self):
        self._indicators: dict[str, dict] = {}

    def get_source_last_updated(self, indicator_id: str) -> str | None:
        stored = self._indicators.get(indicator_id)
        return stored["metadata"]["source_last_updated"] if stored else None

    def save_indicator(self, indicator_id: str, metadata: dict, rows: dict) -> None:
        self._indicators[indicator_id] = {
            "metadata": {k: metadata.get(k) for k in _METADATA_FIELDS},
            "rows": dict(rows),
        }

    def get_indicator(self, indicator_id: str) -> dict | None:
        stored = self._indicators.get(indicator_id)
        if stored is None:
            return None
        return {"metadata": dict(stored["metadata"]), "rows": dict(stored["rows"])}
