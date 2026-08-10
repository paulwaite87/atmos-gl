from datetime import datetime, timezone

from atmos_gl.db.engine import Session
from atmos_gl.db.models import UserSettings


class UserSettingsAdapter:
    """Real adapter for user_settings (issue #305/#314) -- a sparse
    {section: {option: value}} override map per user."""

    def get_overrides(self, user_id: int) -> dict:
        with Session() as session:
            row = session.get(UserSettings, user_id)
            return dict(row.overrides) if row is not None else {}

    def merge_section(self, user_id: int, section: str, values: dict) -> dict:
        """Deep-merges `values` into this user's stored overrides for `section` only.
        Every other section's overrides, and any key already stored in this section
        but absent from `values`, are left untouched -- never replaces the whole
        stored map (see #313's near-miss with POST /api/config's whole-tree replace
        semantics, which this deliberately does not repeat). Reassigns `row.overrides`
        to a brand-new dict rather than mutating the existing one in place, so
        SQLAlchemy's change tracking sees it without needing a MutableDict wrapper."""
        with Session() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                row = UserSettings(user_id=user_id, overrides={})
                session.add(row)
            overrides = dict(row.overrides or {})
            section_overrides = dict(overrides.get(section, {}))
            section_overrides.update(values)
            overrides[section] = section_overrides
            row.overrides = overrides
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return dict(row.overrides)

    def clear_section(self, user_id: int, section: str) -> dict:
        """Removes `section` entirely from this user's stored overrides, reverting
        every key in it to whatever the current global default is (#314's reset
        action) -- a no-op if the user has no row yet or no overrides for it."""
        with Session() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                return {}
            overrides = dict(row.overrides or {})
            overrides.pop(section, None)
            row.overrides = overrides
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return dict(row.overrides)


class FakeUserSettingsAdapter:
    """In-memory fake for user_settings, matching UserSettingsAdapter's method
    contracts."""

    def __init__(self):
        self._overrides: dict[int, dict] = {}

    def get_overrides(self, user_id: int) -> dict:
        return dict(self._overrides.get(user_id, {}))

    def merge_section(self, user_id: int, section: str, values: dict) -> dict:
        overrides = self._overrides.setdefault(user_id, {})
        section_overrides = dict(overrides.get(section, {}))
        section_overrides.update(values)
        overrides[section] = section_overrides
        return dict(overrides)

    def clear_section(self, user_id: int, section: str) -> dict:
        overrides = self._overrides.setdefault(user_id, {})
        overrides.pop(section, None)
        return dict(overrides)
