#!/usr/bin/env python3
"""Guard against VolcanoAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeVolcanoAdapter hand-reimplements
VolcanoAdapter's on-conflict SQL in Python independently (vei/significant/
erupt_date_code update, name/lat/lon/geom stay immutable), so if they ever diverge,
nothing else would catch it. tests/test_volcano_adapter.py exercises only the Fake.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.volcano_adapter import VolcanoAdapter, FakeVolcanoAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return VolcanoAdapter(), patch("atmos_gl.db.volcano_adapter.Session", TestSession)
    return FakeVolcanoAdapter(), contextlib.nullcontext()


def _row(adapter, v_id, real_db):
    if isinstance(adapter, FakeVolcanoAdapter):
        row = adapter._volcanoes[v_id]
        return {"name": row["name"], "lat": row["lat"], "lon": row["lon"],
                "vei": row["vei"], "significant": row["significant"],
                "erupt_date_code": row["erupt_date_code"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text(
                "SELECT name, lat, lon, vei, significant, erupt_date_code "
                "FROM volcanoes WHERE id = :id"
            ),
            {"id": v_id},
        ).mappings().one()
        return dict(result)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_vei_significant_date_code_update_on_conflict(kind, real_db):
    v_id = f"volcano-update-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_volcano(v_id, "Original Name", -6.1, 155.2, 1, False, "2026-01")
        adapter.update_volcano(v_id, "Renamed", -6.1, 155.2, 4, True, "2026-02")
        row = _row(adapter, v_id, real_db)

    assert row["vei"] == 4
    assert row["significant"] is True
    assert row["erupt_date_code"] == "2026-02"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_name_lat_lon_immutable_on_conflict(kind, real_db):
    """The SQL on_conflict_do_update's set_ dict omits name/lat/lon/geom entirely -- a
    later report can't rename or relocate an existing volcano, matching the Fake's
    independent omission of those fields from its update branch."""
    v_id = f"volcano-immutable-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.update_volcano(v_id, "Original Name", -6.1, 155.2, 1, False, "2026-01")
        adapter.update_volcano(v_id, "Different Name", 10.0, 20.0, 2, False, "2026-01")
        row = _row(adapter, v_id, real_db)

    assert row["name"] == "Original Name"
    assert row["lat"] == pytest.approx(-6.1)
    assert row["lon"] == pytest.approx(155.2)
