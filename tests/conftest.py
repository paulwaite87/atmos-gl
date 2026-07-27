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
    yield TestClient(app)
    app.dependency_overrides.clear()


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
