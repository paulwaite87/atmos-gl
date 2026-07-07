#!/usr/bin/env python3
"""Shared pytest fixtures. `client` is for route-level tests that exercise a real
FastAPI route via app.dependency_overrides (the "router seam for the Fakes"
architecture-review candidate) -- clearing overrides after each test keeps one
test's substituted adapter from leaking into the next.

`real_db` is for Real/Fake adapter drift tests (architecture review candidate "guard
against Real/Fake adapter drift") -- a throwaway postgis/postgis container, migrated
with the SAME `alembic upgrade head` production uses, session-scoped so the container
only starts once. Matches docker-compose.yml's worldmap_db image+version exactly for
fidelity. Adapters under test still need their own `Session` monkeypatched to point at
this engine (see test_ship_adapter_real_vs_fake.py) -- worldmap.db.engine.Session is
bound at import time to the real PGHOST/etc., and each adapter module imports that
name directly, so patching worldmap.db.engine alone doesn't reach them.
"""
import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from testcontainers.postgres import PostgresContainer

from worldmap.api import app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def real_db():
    with PostgresContainer(
        "postgis/postgis:15-3.3",  # matches docker-compose.yml's worldmap_db exactly
        username="wmap_test",
        password="wmap_test",
        dbname="worldmap_test",
        driver="psycopg2",
    ) as pg:
        env = os.environ.copy()
        env.update(
            PGHOST=pg.get_container_host_ip(),
            PGPORT=str(pg.get_exposed_port(5432)),
            PGUSER="wmap_test",
            PGPASSWORD="wmap_test",
            PGDATABASE="worldmap_test",
        )
        subprocess.run(
            ["alembic", "upgrade", "head"], env=env, cwd=REPO_ROOT, check=True
        )
        engine = create_engine(pg.get_connection_url())
        yield engine
        engine.dispose()
