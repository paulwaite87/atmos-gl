#!/usr/bin/env python3
"""Shared pytest fixtures. `client` is for route-level tests that exercise a real
FastAPI route via app.dependency_overrides (the "router seam for the Fakes"
architecture-review candidate) -- clearing overrides after each test keeps one
test's substituted adapter from leaking into the next."""
import pytest
from fastapi.testclient import TestClient

from worldmap.api import app


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()
