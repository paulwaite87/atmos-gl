#!/usr/bin/env python3
"""Guard against ViewportAdapter Real/Fake drift, matching the pattern
test_process_status_adapter_real_vs_fake.py established."""
import contextlib

import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from atmos_gl.db.viewport_adapter import ViewportAdapter, FakeViewportAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return ViewportAdapter(), patch("atmos_gl.db.viewport_adapter.Session", TestSession)
    return FakeViewportAdapter(), contextlib.nullcontext()


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_viewport_is_none_before_any_update(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        assert adapter.get_viewport() is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_update_then_get_roundtrips(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.update_viewport(lat=-41.3, lon=174.8, zoom=5.5)
        row = adapter.get_viewport()

        assert row["lat"] == pytest.approx(-41.3)
        assert row["lon"] == pytest.approx(174.8)
        assert row["zoom"] == pytest.approx(5.5)
        assert row["updated_at"] is not None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_second_update_overwrites_the_single_row(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.update_viewport(lat=0.0, lon=0.0, zoom=1.0)
        adapter.update_viewport(lat=51.5, lon=-0.1, zoom=8.0)
        row = adapter.get_viewport()

        assert row["lat"] == pytest.approx(51.5)
        assert row["lon"] == pytest.approx(-0.1)
        assert row["zoom"] == pytest.approx(8.0)
