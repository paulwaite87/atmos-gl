#!/usr/bin/env python3
"""Guard against VolcanicActivityAdapter Real/Fake drift, matching the pattern
test_ship_adapter_real_vs_fake.py established: FakeVolcanicActivityAdapter
hand-reimplements the real adapter's ON CONFLICT coalesce semantics in Python
independently (a None field never clobbers an existing value), so if they ever
diverge, nothing else would catch it. tests/test_volcanic_activity_adapter.py
exercises only the Fake.
"""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.volcanic_activity_adapter import VolcanicActivityAdapter, FakeVolcanicActivityAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return VolcanicActivityAdapter(), patch("atmos_gl.db.volcanic_activity_adapter.Session", TestSession)
    return FakeVolcanicActivityAdapter(), contextlib.nullcontext()


def _row(adapter, vnum, real_db):
    if isinstance(adapter, FakeVolcanicActivityAdapter):
        row = adapter._activity[vnum]
        return {k: row[k] for k in ("name", "country", "lat", "lon", "hans_color_code", "hans_alert_level")}
    with real_db.connect() as conn:
        result = conn.execute(
            text(
                "SELECT name, country, lat, lon, hans_color_code, hans_alert_level "
                "FROM volcanic_activity WHERE vnum = :vnum"
            ),
            {"vnum": vnum},
        ).mappings().one()
        return dict(result)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_hans_only_update_does_not_clobber_name_lat_lon(kind, real_db):
    vnum = f"va-hans-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_activity(
            vnum, "Original Name", "Country", -6.1, 155.2,
            "New Unrest", "First report.", None, None, None,
        )
        adapter.upsert_activity(
            vnum, None, None, None, None, None, None, "ORANGE", "WATCH", "https://example.com",
        )
        row = _row(adapter, vnum, real_db)

    assert row["name"] == "Original Name"
    assert row["country"] == "Country"
    assert row["lat"] == pytest.approx(-6.1)
    assert row["lon"] == pytest.approx(155.2)
    assert row["hans_color_code"] == "ORANGE"
    assert row["hans_alert_level"] == "WATCH"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_fresh_gvp_sighting_updates_name_lat_lon(kind, real_db):
    vnum = f"va-gvp-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.upsert_activity(
            vnum, "Original Name", "Country", -6.1, 155.2,
            "New Unrest", "First report.", None, None, None,
        )
        adapter.upsert_activity(
            vnum, "Updated Name", "Country", 10.0, 20.0,
            "Continuing Eruptive Activity", "Second report.", None, None, None,
        )
        row = _row(adapter, vnum, real_db)

    assert row["name"] == "Updated Name"
    assert row["lat"] == pytest.approx(10.0)
    assert row["lon"] == pytest.approx(20.0)
