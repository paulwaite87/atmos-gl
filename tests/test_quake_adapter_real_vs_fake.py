#!/usr/bin/env python3
"""Guard against QuakeAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeQuakeAdapter hand-reimplements
QuakeAdapter's on-conflict SQL in Python independently (mag/depth/place/eq_time update,
lat/lon/geom stay immutable), so if they ever diverge, nothing else would catch it.
tests/test_quake_adapter.py exercises only the Fake.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.quake_adapter import QuakeAdapter, FakeQuakeAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return QuakeAdapter(), patch("atmos_gl.db.quake_adapter.Session", TestSession)
    return FakeQuakeAdapter(), contextlib.nullcontext()


def _row(adapter, quake_id, real_db):
    if isinstance(adapter, FakeQuakeAdapter):
        row = adapter._quakes[quake_id]
        return {"mag": row["mag"], "depth": row["depth"], "place": row["place"],
                "lat": row["lat"], "lon": row["lon"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text("SELECT mag, depth, place, lat, lon FROM earthquakes WHERE id = :id"),
            {"id": quake_id},
        ).mappings().one()
        return dict(result)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_mag_depth_place_update_on_conflict(kind, real_db):
    quake_id = f"quake-update-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_quake(quake_id, 4.5, 10.0, "Original Place", "2026-01-01T00:00:00+00:00", -36.8, 174.7)
        adapter.update_quake(quake_id, 5.1, 12.5, "Updated Place", "2026-01-01T01:00:00+00:00", -36.8, 174.7)
        row = _row(adapter, quake_id, real_db)

    assert row["mag"] == pytest.approx(5.1)
    assert row["depth"] == pytest.approx(12.5)
    assert row["place"] == "Updated Place"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_lat_lon_immutable_on_conflict(kind, real_db):
    """The SQL on_conflict_do_update's set_ dict omits lat/lon/geom entirely -- a later
    report with different coordinates for the same id must not move the epicenter,
    matching the Fake's independent omission of lat/lon from its update branch."""
    quake_id = f"quake-latlon-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_quake(quake_id, 4.5, 10.0, "Place", "2026-01-01T00:00:00+00:00", -36.8, 174.7)
        adapter.update_quake(quake_id, 4.6, 10.0, "Place", "2026-01-01T00:05:00+00:00", 10.0, 20.0)
        row = _row(adapter, quake_id, real_db)

    assert row["lat"] == pytest.approx(-36.8)
    assert row["lon"] == pytest.approx(174.7)
