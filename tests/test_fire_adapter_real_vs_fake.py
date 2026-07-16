#!/usr/bin/env python3
"""Guard against FireAdapter Real/Fake drift, mirroring
test_quake_adapter_real_vs_fake.py: FakeFireAdapter hand-reimplements FireAdapter's
on-conflict SQL in Python independently (brightness/frp/confidence update, lat/lon/geom/
satellite/daynight/acq_time stay immutable), so if they ever diverge, nothing else would
catch it. tests/test_fire_adapter.py exercises only the Fake.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.fire_adapter import FireAdapter, FakeFireAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return FireAdapter(), patch("atmos_gl.db.fire_adapter.Session", TestSession)
    return FakeFireAdapter(), contextlib.nullcontext()


def _row(adapter, fire_id, real_db):
    if isinstance(adapter, FakeFireAdapter):
        row = adapter._fires[fire_id]
        return {"brightness": row["brightness"], "frp": row["frp"], "confidence": row["confidence"],
                "lat": row["lat"], "lon": row["lon"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text("SELECT brightness, frp, confidence, lat, lon FROM fires WHERE id = :id"),
            {"id": fire_id},
        ).mappings().one()
        return dict(result)


def _fire_row(fire_id, lat, lon, brightness, frp, confidence, acq_time_iso):
    return {
        "id": fire_id, "lat": lat, "lon": lon, "brightness": brightness, "frp": frp,
        "confidence": confidence, "satellite": "N", "daynight": "D", "acq_time": acq_time_iso,
    }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_brightness_frp_confidence_update_on_conflict(kind, real_db):
    fire_id = f"fire-update-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_fires([_fire_row(fire_id, -36.8, 174.7, 320.0, 8.0, "low", "2026-01-01T00:00:00+00:00")])
        adapter.upsert_fires([_fire_row(fire_id, -36.8, 174.7, 340.0, 15.0, "high", "2026-01-01T00:00:00+00:00")])
        row = _row(adapter, fire_id, real_db)

    assert row["brightness"] == pytest.approx(340.0)
    assert row["frp"] == pytest.approx(15.0)
    assert row["confidence"] == "high"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_lat_lon_immutable_on_conflict(kind, real_db):
    """The SQL on_conflict_do_update's set_ dict omits lat/lon/geom entirely -- a later
    report with different coordinates for the same id must not move the detection,
    matching the Fake's independent omission of lat/lon from its update branch."""
    fire_id = f"fire-latlon-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_fires([_fire_row(fire_id, -36.8, 174.7, 320.0, 8.0, "low", "2026-01-01T00:00:00+00:00")])
        adapter.upsert_fires([_fire_row(fire_id, 10.0, 20.0, 320.0, 8.0, "low", "2026-01-01T00:05:00+00:00")])
        row = _row(adapter, fire_id, real_db)

    assert row["lat"] == pytest.approx(-36.8)
    assert row["lon"] == pytest.approx(174.7)
