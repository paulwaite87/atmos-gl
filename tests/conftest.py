#!/usr/bin/env python3
"""Shared pytest fixtures. `client` is for route-level tests that exercise a real
FastAPI route via app.dependency_overrides (the "router seam for the Fakes"
architecture-review candidate) -- clearing overrides after each test keeps one
test's substituted adapter from leaking into the next.

`real_db` is for Real/Fake adapter drift tests (architecture review candidate "guard
against Real/Fake adapter drift") -- a throwaway postgis/postgis container, migrated
with the SAME `alembic upgrade head` production uses, session-scoped so the container
only starts once. Matches docker-compose.yml's atmos_gl_db image+version exactly for
fidelity. Adapters under test still need their own `Session` monkeypatched to point at
this engine (see test_ship_adapter_real_vs_fake.py) -- atmos_gl.db.engine.Session is
bound at import time to the real POSTGRES_HOST/etc., and each adapter module imports that
name directly, so patching atmos_gl.db.engine alone doesn't reach them.
"""
import io
import os
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from testcontainers.postgres import PostgresContainer

from atmos_gl.api import app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def make_netcdf_zip_bytes():
    """Factory fixture: make_netcdf_zip_bytes(nc_filename, content) -> bytes, a
    minimal data_format=netcdf_zip-shaped archive (one .nc member) for testing the
    CDS-backed greenhouse-gas collectors' unzip step, without a real netCDF or
    network call. Shared by the CAMS forecast and EGG4 baseline collector tests."""

    def _make(nc_filename: str, content: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(nc_filename, content)
        return buf.getvalue()

    return _make


@pytest.fixture
def client():
    """Each test gets its own fresh RateLimiter instances (issue #307) rather than
    sharing the app's process-wide singletons -- TestClient's IP is always
    "testclient" and FakeUserAdapter ids restart at 1 per test, so without this two
    unrelated tests could trip each other's rate limit. Kept at the real production
    limits (not disabled outright) so a test that deliberately wants to exercise the
    429 path can just re-override with a tighter one afterward."""
    from atmos_gl.lib.rate_limit import RateLimiter
    from atmos_gl.routes.auth import get_login_rate_limiter
    from atmos_gl.routes.me_settings import get_settings_rate_limiter

    # Constructed once and captured by reference -- FastAPI calls an override callable
    # fresh on every request, so a lambda that builds a new RateLimiter() inline would
    # reset its window on every single call and never actually accumulate hits.
    login_limiter = RateLimiter(max_requests=20, window_seconds=300)
    settings_limiter = RateLimiter(max_requests=30, window_seconds=60)
    app.dependency_overrides[get_login_rate_limiter] = lambda: login_limiter
    app.dependency_overrides[get_settings_rate_limiter] = lambda: settings_limiter
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_signed_in_session(*, is_admin: bool = True):
    """(fake_user_adapter, session_token) for a signed-in user -- shared by admin_client
    below and any test file authenticating the `client` fixture for routes gated by
    routes.auth.require_admin (issue #304). Sets is_admin directly on the fake rather
    than going through ADMIN_EMAILS/is_admin_email -- these tests exercise the route
    gate, not admin recognition itself (see tests/test_lib_auth.py for that). Not a
    fixture itself (parametrized by is_admin) -- imported directly by test files that
    need a non-admin session too (e.g. tests/test_admin_gated_routes.py's 403 cases)."""
    from atmos_gl.db.user_adapter import FakeUserAdapter

    fake = FakeUserAdapter()
    user = fake.get_or_create_user(
        email="admin@example.com" if is_admin else "visitor@example.com",
        name="Admin" if is_admin else "Visitor",
        provider="google", provider_user_id="sub-1",
    )
    fake._users[user["id"]]["is_admin"] = is_admin
    token = fake.create_session(user["id"], ttl_seconds=3600)
    return fake, token


@pytest.fixture
def admin_client(client):
    """`client`, pre-authenticated as an admin user (issue #304's admin-gated routes)."""
    from atmos_gl.lib.auth import SESSION_COOKIE_NAME
    from atmos_gl.routes.auth import get_user_adapter

    fake, token = make_signed_in_session(is_admin=True)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


@pytest.fixture(scope="session")
def real_db():
    with PostgresContainer(
        "postgis/postgis:15-3.3",  # matches docker-compose.yml's atmos_gl_db exactly
        username="agl_test",
        password="agl_test",
        dbname="atmos_gl_test",
        driver="psycopg2",
    ) as pg:
        env = os.environ.copy()
        env.update(
            POSTGRES_HOST=pg.get_container_host_ip(),
            POSTGRES_PORT=str(pg.get_exposed_port(5432)),
            POSTGRES_USER="agl_test",
            POSTGRES_PASSWORD="agl_test",
            POSTGRES_DB="atmos_gl_test",
        )
        subprocess.run(
            ["alembic", "upgrade", "head"], env=env, cwd=REPO_ROOT, check=True
        )
        engine = create_engine(pg.get_connection_url())
        yield engine
        engine.dispose()
