#!/usr/bin/env python3
"""Guard against UserAdapter Real/Fake drift, matching the pattern
test_viewport_adapter_real_vs_fake.py established (issue #303)."""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.user_adapter import UserAdapter, FakeUserAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return UserAdapter(), patch("atmos_gl.db.user_adapter.Session", TestSession)
    return FakeUserAdapter(), contextlib.nullcontext()


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_or_create_user_creates_a_new_user_on_first_login(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        user = adapter.get_or_create_user(
            email="visitor@example.com", name="Visitor",
            provider="google", provider_user_id="google-sub-1",
        )
        assert user["email"] == "visitor@example.com"
        assert user["name"] == "Visitor"
        assert user["is_admin"] is False
        assert user["id"] is not None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_or_create_user_sets_is_admin_from_admin_emails(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {"ADMIN_EMAILS": "boss@example.com"}):
        user = adapter.get_or_create_user(
            email="boss@example.com", name="Boss",
            provider="google", provider_user_id="google-sub-2",
        )
        assert user["is_admin"] is True


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_repeat_login_with_same_identity_returns_the_same_user(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        first = adapter.get_or_create_user(
            email="repeat@example.com", name="Old Name",
            provider="google", provider_user_id="google-sub-3",
        )
        second = adapter.get_or_create_user(
            email="repeat@example.com", name="New Name",
            provider="google", provider_user_id="google-sub-3",
        )
        assert second["id"] == first["id"]
        assert second["name"] == "New Name"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_repeat_login_refreshes_is_admin(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        with patch.dict("os.environ", {}, clear=True):
            first = adapter.get_or_create_user(
                email="promoted@example.com", name="Someone",
                provider="google", provider_user_id="google-sub-4",
            )
            assert first["is_admin"] is False
        with patch.dict("os.environ", {"ADMIN_EMAILS": "promoted@example.com"}):
            second = adapter.get_or_create_user(
                email="promoted@example.com", name="Someone",
                provider="google", provider_user_id="google-sub-4",
            )
            assert second["id"] == first["id"]
            assert second["is_admin"] is True


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_a_second_provider_with_the_same_email_reuses_the_same_user(kind, real_db):
    """Validates issue #303's identity-design requirement: adding a provider is a new
    user_identities row, never a schema change or a duplicate user."""
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        via_google = adapter.get_or_create_user(
            email="multi@example.com", name="Multi",
            provider="google", provider_user_id="google-sub-5",
        )
        via_github = adapter.get_or_create_user(
            email="multi@example.com", name="Multi",
            provider="github", provider_user_id="github-sub-5",
        )
        assert via_github["id"] == via_google["id"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_create_session_then_get_session_user_roundtrips(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        user = adapter.get_or_create_user(
            email="sess@example.com", name="Sess",
            provider="google", provider_user_id="google-sub-6",
        )
        token = adapter.create_session(user["id"], ttl_seconds=3600)
        assert isinstance(token, str) and len(token) > 20

        fetched = adapter.get_session_user(token)
        assert fetched["id"] == user["id"]
        assert fetched["email"] == "sess@example.com"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_session_user_with_an_unknown_token_returns_none(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        assert adapter.get_session_user("not-a-real-token") is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_expired_session_is_not_returned(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        user = adapter.get_or_create_user(
            email="expired@example.com", name="Expired",
            provider="google", provider_user_id="google-sub-7",
        )
        token = adapter.create_session(user["id"], ttl_seconds=-1)
        assert adapter.get_session_user(token) is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_delete_session_revokes_it(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx, patch.dict("os.environ", {}, clear=True):
        user = adapter.get_or_create_user(
            email="revoke@example.com", name="Revoke",
            provider="google", provider_user_id="google-sub-8",
        )
        token = adapter.create_session(user["id"], ttl_seconds=3600)
        assert adapter.get_session_user(token) is not None

        adapter.delete_session(token)
        assert adapter.get_session_user(token) is None
