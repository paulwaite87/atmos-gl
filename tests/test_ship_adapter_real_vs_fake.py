#!/usr/bin/env python3
"""Guard against ShipAdapter Real/Fake drift (architecture review candidate "guard
against Real/Fake adapter drift"). FakeShipAdapter hand-reimplements ShipAdapter's SQL
CASE logic in Python independently; tests/test_ship_adapter.py exercises only the
Fake, so if the SQL and the Fake ever diverge, nothing catches it. These tests run the
SAME scenario and assertions against both, via a real, throwaway postgis container
(see conftest.py's real_db fixture) migrated with the real `alembic upgrade head`.

Scoped to the three genuinely delicate conditional-update clauses the review flagged --
prev_draught stickiness, name stickiness, vessel_type stickiness. field_catalog_adapter
was considered too (the review's other candidate) but its upsert turned out to be a
plain unconditional overwrite-on-conflict, nothing to drift on -- out of scope here.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.ship_adapter import ShipAdapter, FakeShipAdapter


def _make_adapter(kind, real_db):
    """Returns (adapter, context_manager) -- the context manager patches
    ship_adapter.Session to the real_db engine for "real", or is a no-op for "fake"."""
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return ShipAdapter(), patch("atmos_gl.db.ship_adapter.Session", TestSession)
    return FakeShipAdapter(), contextlib.nullcontext()


def _row(adapter, mmsi, real_db):
    if isinstance(adapter, FakeShipAdapter):
        row = adapter._ships[mmsi]
        return {"draught": row["draught"], "prev_draught": row["prev_draught"],
                "name": row["name"], "vessel_type": row["vessel_type"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text(
                "SELECT draught, prev_draught, name, vessel_type FROM ships WHERE mmsi = :mmsi"
            ),
            {"mmsi": mmsi},
        ).mappings().one()
        return dict(result)


def _static_body(draught):
    return {"ShipName": "MV Test"}, {
        "Destination": "Auckland",
        "Type": 70,
        "ImoNumber": 123456,
        "CallSign": "TEST1",
        "MaximumStaticDraught": draught,
        "Dimension": {"A": 50, "B": 50, "C": 10, "D": 10},
    }


def _position_body(name, vessel_type):
    return (
        {
            "ShipName": name,
            "Latitude": -36.8,
            "Longitude": 174.7,
            "time_utc": "2026-01-01 00:00:00.000 +0000",
        },
        {"Type": vessel_type, "NavigationalStatus": 0, "Cog": 90.0, "Sog": 12.0},
    )


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_prev_draught_becomes_sticky_on_real_change(kind, real_db):
    mmsi = f"draught-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_static_data(mmsi, *_static_body(5.0))
        adapter.update_ship_static_data(mmsi, *_static_body(3.0))
        row = _row(adapter, mmsi, real_db)

    assert row["draught"] == pytest.approx(3.0)
    assert row["prev_draught"] == pytest.approx(5.0)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_prev_draught_unchanged_when_draught_repeats(kind, real_db):
    mmsi = f"draught-repeat-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_static_data(mmsi, *_static_body(5.0))
        adapter.update_ship_static_data(mmsi, *_static_body(3.0))
        adapter.update_ship_static_data(mmsi, *_static_body(3.0))  # same draught again
        row = _row(adapter, mmsi, real_db)

    assert row["draught"] == pytest.approx(3.0)
    assert row["prev_draught"] == pytest.approx(5.0)  # unchanged, not overwritten to 3.0


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_prev_draught_ignores_a_zero_update(kind, real_db):
    """excluded.draught > 0 in the SQL CASE -- a zero/missing draught report must not
    clobber prev_draught, matching the Fake's `draught > 0` guard."""
    mmsi = f"draught-zero-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_static_data(mmsi, *_static_body(5.0))
        adapter.update_ship_static_data(mmsi, *_static_body(0.0))  # bogus zero report
        row = _row(adapter, mmsi, real_db)

    assert row["draught"] == pytest.approx(0.0)
    assert row["prev_draught"] == pytest.approx(0.0)  # never had a real change to record


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_name_is_sticky_against_blank_or_unknown_reports(kind, real_db):
    mmsi = f"name-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_position_data(mmsi, *_position_body("MV First", 0))
        adapter.update_ship_position_data(mmsi, *_position_body("Unknown", 0))
        row = _row(adapter, mmsi, real_db)

    assert row["name"] == "MV First"  # blank/Unknown report doesn't clobber the real name


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_name_updates_on_a_real_report(kind, real_db):
    mmsi = f"name-update-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_position_data(mmsi, *_position_body("MV First", 0))
        adapter.update_ship_position_data(mmsi, *_position_body("MV Renamed", 0))
        row = _row(adapter, mmsi, real_db)

    assert row["name"] == "MV Renamed"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_vessel_type_is_sticky_once_set(kind, real_db):
    mmsi = f"vtype-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_position_data(mmsi, *_position_body("MV Type", 70))  # first real type
        adapter.update_ship_position_data(mmsi, *_position_body("MV Type", 99))  # a different type
        row = _row(adapter, mmsi, real_db)

    assert row["vessel_type"] == 70  # sticky -- first non-zero type wins


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_vessel_type_fills_in_from_zero(kind, real_db):
    mmsi = f"vtype-fill-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_ship_position_data(mmsi, *_position_body("MV Fill", 0))  # unset
        adapter.update_ship_position_data(mmsi, *_position_body("MV Fill", 70))  # first real type
        row = _row(adapter, mmsi, real_db)

    assert row["vessel_type"] == 70
