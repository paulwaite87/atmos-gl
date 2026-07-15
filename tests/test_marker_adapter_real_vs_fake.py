#!/usr/bin/env python3
"""Guard against MarkerAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeMarkerAdapter hand-reimplements
MarkerAdapter's on-conflict SQL in Python independently -- upsert_markers deliberately
never touches the wx_* columns on conflict, so a re-import of the static geojson
preserves the last-sampled weather. If the SQL's set_ dict and the Fake's field list
ever diverge, nothing else would catch it. tests/test_marker_adapter.py exercises only
the Fake.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.marker_adapter import MarkerAdapter, FakeMarkerAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return MarkerAdapter(), patch("atmos_gl.db.marker_adapter.Session", TestSession)
    return FakeMarkerAdapter(), contextlib.nullcontext()


def _row(adapter, marker_id, real_db):
    if isinstance(adapter, FakeMarkerAdapter):
        row = adapter._markers[marker_id]
        return {"name": row["name"], "wx_temp_c": row["wx_temp_c"],
                "wx_humidity_pct": row["wx_humidity_pct"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text("SELECT name, wx_temp_c, wx_humidity_pct FROM markers WHERE id = :id"),
            {"id": marker_id},
        ).mappings().one()
        return dict(result)


def _marker_row(marker_id, name="Auckland", **overrides):
    row = {
        "id": marker_id, "name": name, "kind": "city", "country": "NZ",
        "priority": 1, "pop": 1000000, "capital": False, "color": "#fff",
        "timezone": "Pacific/Auckland", "lat": -36.8, "lon": 174.7,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_wx_defaults_to_null_on_first_insert(kind, real_db):
    marker_id = f"marker-insert-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_markers([_marker_row(marker_id)])
        row = _row(adapter, marker_id, real_db)

    assert row["wx_temp_c"] is None
    assert row["wx_humidity_pct"] is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_wx_survives_a_static_data_reimport(kind, real_db):
    """A re-import of markers.geojson (e.g. after adding a new marker) must not reset
    an existing marker's last-sampled weather, even though the static fields update."""
    marker_id = f"marker-wx-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_markers([_marker_row(marker_id)])
        adapter.update_marker_weather(
            [{"id": marker_id, "t": 18.5, "rh": 60, "ws": 3.2, "wd": 270, "valid_time": None}]
        )
        adapter.upsert_markers([_marker_row(marker_id, name="Auckland City")])
        row = _row(adapter, marker_id, real_db)

    assert row["name"] == "Auckland City"
    assert row["wx_temp_c"] == pytest.approx(18.5)
    assert row["wx_humidity_pct"] == pytest.approx(60)
