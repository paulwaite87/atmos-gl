#!/usr/bin/env python3
"""Guard against FrontlineAdapter Real/Fake drift: the fake hand-reimplements the
insert-once semantics and "latest snapshot" ordering independently."""
import contextlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.frontline_adapter import FakeFrontlineAdapter, FrontlineAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return FrontlineAdapter(), patch("atmos_gl.db.frontline_adapter.Session", TestSession)
    return FakeFrontlineAdapter(), contextlib.nullcontext()


def _fc(status):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"status": status},
        "geometry": {"type": "Polygon", "coordinates": [[[37, 48], [38, 48], [38, 49], [37, 48]]]},
    }]}


# The real table is shared across tests, so each test's snapshots are dated far enough
# ahead (and distinctly per kind) to be the latest ones.
def _at(kind, day):
    return datetime(2100 if kind == "real" else 2101, 1, day, tzinfo=timezone.utc)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_latest_snapshot_is_served_with_its_metadata(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    base = 9_000_000_000 + (0 if kind == "real" else 100)
    with ctx:
        adapter.save_snapshot(base + 1, _at(kind, 1), "Older", _fc("contested"))
        adapter.save_snapshot(base + 2, _at(kind, 2), "The enemy advanced near X.", _fc("occupied"))
        body = json.loads(adapter.get_latest_geojson())

    assert body["type"] == "FeatureCollection"
    assert [f["properties"]["status"] for f in body["features"]] == ["occupied"]
    assert body["snapshot"] == {
        "id": base + 2, "created_at": _at(kind, 2).isoformat(),
        "description": "The enemy advanced near X.",
    }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_saving_a_known_snapshot_is_a_no_op(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    snapshot_id = 9_000_000_010 + (0 if kind == "real" else 100)
    with ctx:
        assert not adapter.has_snapshot(snapshot_id)
        adapter.save_snapshot(snapshot_id, _at(kind, 3), "First", _fc("occupied"))
        adapter.save_snapshot(snapshot_id, _at(kind, 3), "Second", _fc("contested"))
        assert adapter.has_snapshot(snapshot_id)
        body = json.loads(adapter.get_latest_geojson())

    assert body["snapshot"]["description"] == "First"
    assert body["features"][0]["properties"]["status"] == "occupied"


def test_no_snapshot_yet_is_an_empty_collection():
    assert json.loads(FakeFrontlineAdapter().get_latest_geojson()) == {
        "type": "FeatureCollection", "features": [],
    }
