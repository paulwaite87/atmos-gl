#!/usr/bin/env python3
"""Guard against UserSettingsAdapter Real/Fake drift, matching the pattern
test_user_adapter_real_vs_fake.py established (issue #305/#314)."""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.user_adapter import UserAdapter
from atmos_gl.db.user_settings_adapter import UserSettingsAdapter, FakeUserSettingsAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return UserSettingsAdapter(), patch("atmos_gl.db.user_settings_adapter.Session", TestSession), TestSession
    return FakeUserSettingsAdapter(), contextlib.nullcontext(), None


def _make_user_id(kind, TestSession, email):
    """user_settings.user_id is a real FK to users.id -- the real-DB case needs an
    actual row there first; the fake case has no such constraint, so any id works."""
    if kind == "fake":
        return 1
    with patch("atmos_gl.db.user_adapter.Session", TestSession):
        user = UserAdapter().get_or_create_user(
            email=email, name="Test", provider="google", provider_user_id=email,
        )
        return user["id"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_overrides_is_empty_for_a_user_with_no_row(kind, real_db):
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "no-overrides@example.com")
        assert adapter.get_overrides(user_id) == {}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_merge_section_creates_a_row_and_stores_the_values(kind, real_db):
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "merge1@example.com")
        result = adapter.merge_section(user_id, "precipitation", {"palette": "ocean_blue"})
        assert result == {"precipitation": {"palette": "ocean_blue"}}
        assert adapter.get_overrides(user_id) == {"precipitation": {"palette": "ocean_blue"}}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_merge_section_only_updates_given_keys_leaving_others_in_the_section_untouched(kind, real_db):
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "merge2@example.com")
        adapter.merge_section(user_id, "precipitation", {"palette": "ocean_blue", "opacity": 50})
        result = adapter.merge_section(user_id, "precipitation", {"opacity": 80})
        assert result == {"precipitation": {"palette": "ocean_blue", "opacity": 80}}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_merge_section_never_touches_a_different_section(kind, real_db):
    """The exact near-miss #313's implementation hit and fixed (POST /api/config's
    whole-tree replace) -- merge_section must only ever touch the section it's told to."""
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "merge3@example.com")
        adapter.merge_section(user_id, "precipitation", {"palette": "ocean_blue"})
        adapter.merge_section(user_id, "sst", {"palette": "ocean"})
        assert adapter.get_overrides(user_id) == {
            "precipitation": {"palette": "ocean_blue"},
            "sst": {"palette": "ocean"},
        }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_clear_section_removes_only_that_section(kind, real_db):
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "clear1@example.com")
        adapter.merge_section(user_id, "precipitation", {"palette": "ocean_blue"})
        adapter.merge_section(user_id, "sst", {"palette": "ocean"})
        result = adapter.clear_section(user_id, "precipitation")
        assert result == {"sst": {"palette": "ocean"}}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_clear_section_on_a_user_with_no_row_is_a_no_op(kind, real_db):
    adapter, ctx, TestSession = _make_adapter(kind, real_db)
    with ctx:
        user_id = _make_user_id(kind, TestSession, "clear2@example.com")
        assert adapter.clear_section(user_id, "precipitation") == {}
