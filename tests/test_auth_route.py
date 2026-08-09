#!/usr/bin/env python3
"""Route-level tests for GET /api/auth/me and POST /api/auth/logout (issue #303).
/login/google and /callback/google aren't covered here -- they're thin glue around a
live Google OAuth round-trip, not meaningfully unit-testable without mocking an
external network call this repo doesn't otherwise mock for third-party integrations."""
from atmos_gl.db.user_adapter import FakeUserAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.routes.auth import get_user_adapter
from atmos_gl.api import app


def test_me_reports_unauthenticated_with_no_cookie(client):
    resp = client.get("/api/auth/me")

    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False}


def test_me_reports_unauthenticated_with_an_unknown_session_cookie(client):
    fake = FakeUserAdapter()
    app.dependency_overrides[get_user_adapter] = lambda: fake
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")

    resp = client.get("/api/auth/me")

    assert resp.json() == {"authenticated": False}


def test_me_reports_the_signed_in_user_for_a_valid_session(client):
    fake = FakeUserAdapter()
    user = fake.get_or_create_user(
        email="visitor@example.com", name="Visitor",
        provider="google", provider_user_id="google-sub-1",
    )
    token = fake.create_session(user["id"], ttl_seconds=3600)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    client.cookies.set(SESSION_COOKIE_NAME, token)

    resp = client.get("/api/auth/me")

    assert resp.json() == {
        "authenticated": True,
        "email": "visitor@example.com",
        "name": "Visitor",
        "is_admin": False,
    }


def test_logout_revokes_the_session_and_clears_the_cookie(client):
    fake = FakeUserAdapter()
    user = fake.get_or_create_user(
        email="visitor@example.com", name="Visitor",
        provider="google", provider_user_id="google-sub-1",
    )
    token = fake.create_session(user["id"], ttl_seconds=3600)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    client.cookies.set(SESSION_COOKIE_NAME, token)

    resp = client.post("/api/auth/logout")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    assert fake.get_session_user(token) is None


def test_logout_with_no_cookie_is_a_no_op_success(client):
    fake = FakeUserAdapter()
    app.dependency_overrides[get_user_adapter] = lambda: fake

    resp = client.post("/api/auth/logout")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
