#!/usr/bin/env python3
"""Route-level tests for GET /api/auth/me and POST /api/auth/logout (issue #303).
The actual /login/{provider} and /callback/{provider} OAuth round-trips (issues #303,
#306) aren't covered here -- they're thin glue around a live provider round-trip, not
meaningfully unit-testable without mocking an external network call this repo doesn't
otherwise mock for third-party integrations (their pure, provider-specific parsing
logic IS unit tested, see test_auth_identity_resolution.py). The provider whitelist
check IS covered here, since it 404s before any client/network interaction happens.
require_login (issue #305/#314) is exercised via its real consumers instead
(tests/test_me_settings_route.py's 401 cases), matching how require_admin itself is
only tested via test_admin_gated_routes.py's real gated routes, not in isolation."""
from atmos_gl.db.user_adapter import FakeUserAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.lib.rate_limit import RateLimiter
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


# --- /login/{provider}, /callback/{provider} provider whitelist (issue #306) ---


def test_login_404s_for_an_unregistered_provider(client):
    resp = client.get("/api/auth/login/facebook", follow_redirects=False)

    assert resp.status_code == 404


def test_callback_404s_for_an_unregistered_provider(client):
    resp = client.get("/api/auth/callback/facebook", follow_redirects=False)

    assert resp.status_code == 404


# --- /login/{provider}, /callback/{provider} rate limiting (issue #307) ---


def test_login_429s_after_the_ip_rate_limit_is_exceeded(client):
    """Exercised via the unregistered-provider 404 path (same reasoning as the
    whitelist tests above -- the only part of these routes testable without a live
    OAuth round-trip): the rate-limit dependency runs before the route body's own
    provider check, so this still proves the wiring without touching the network."""
    from atmos_gl.routes.auth import get_login_rate_limiter

    limiter = RateLimiter(max_requests=1, window_seconds=60)
    app.dependency_overrides[get_login_rate_limiter] = lambda: limiter

    first = client.get("/api/auth/login/facebook", follow_redirects=False)
    second = client.get("/api/auth/login/facebook", follow_redirects=False)

    assert first.status_code == 404
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_login_and_callback_share_the_same_ip_rate_limit_budget(client):
    from atmos_gl.routes.auth import get_login_rate_limiter

    limiter = RateLimiter(max_requests=1, window_seconds=60)
    app.dependency_overrides[get_login_rate_limiter] = lambda: limiter

    first = client.get("/api/auth/login/facebook", follow_redirects=False)
    second = client.get("/api/auth/callback/facebook", follow_redirects=False)

    assert first.status_code == 404
    assert second.status_code == 429
