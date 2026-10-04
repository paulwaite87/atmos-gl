#!/usr/bin/env python3
"""Guard against FrontlineAdapter Real/Fake drift: the fake hand-reimplements the
insert-once semantics and "latest snapshot" ordering independently."""
import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
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
        "description": "The enemy advanced near X.", "description_segments": None,
    }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_description_segments_round_trip(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    snapshot_id = 9_000_000_030 + (0 if kind == "real" else 100)
    segments = [{"text": "The enemy advanced near "},
                {"text": "Bilytske", "lat": 48.398, "lon": 37.18, "zoom": 14.0},
                {"text": "."}]
    # Dated before every other test's snapshots, so it never becomes the shared
    # real table's "latest" and disturbs them.
    at = datetime(2099, 1, 1 if kind == "real" else 2, tzinfo=timezone.utc)
    with ctx:
        adapter.save_snapshot(snapshot_id, at, "The enemy advanced near Bilytske.",
                              _fc("occupied"), description_segments=segments)
        stored = adapter.get_snapshot_at(at)

    assert stored["id"] == snapshot_id
    assert stored["description_segments"] == segments


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


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_snapshot_at_is_the_newest_at_or_before_the_time(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    base = 9_000_000_020 + (0 if kind == "real" else 100)
    with ctx:
        adapter.save_snapshot(base + 1, _at(kind, 10), "a", _fc("occupied"))
        adapter.save_snapshot(base + 2, _at(kind, 17), "b", _fc("contested"))
        at_17 = adapter.get_snapshot_at(_at(kind, 17))
        at_16 = adapter.get_snapshot_at(_at(kind, 16))
        before_any = adapter.get_snapshot_at(datetime(1990, 1, 1, tzinfo=timezone.utc))

    assert at_17["id"] == base + 2
    assert at_16 == {"id": base + 1, "created_at": _at(kind, 10), "description": "a",
                     "description_segments": None, "geojson": _fc("occupied")}
    assert before_any is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_prune_keeps_recent_snapshots_and_the_one_current_at_the_cutoff(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    base = 9_000_000_040 + (0 if kind == "real" else 100)
    # Dated after every other test's snapshots so these are the newest in the shared
    # real table (the prune is relative to the newest); removed again afterwards.
    def at(day):
        return datetime(2200, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    ids = {name: base + i for i, name in enumerate(["ancient", "older", "at_cutoff", "recent", "latest"])}
    days = {"ancient": 0, "older": 10, "at_cutoff": 25, "recent": 70, "latest": 100}
    try:
        with ctx:
            for name, snapshot_id in ids.items():
                adapter.save_snapshot(snapshot_id, at(days[name]), name, _fc("occupied"))
            deleted = adapter.prune_older_than(60)   # cutoff: day 40
            survivors = {name for name, snapshot_id in ids.items() if adapter.has_snapshot(snapshot_id)}
            assert adapter.prune_older_than(60) == 0   # idempotent
    finally:
        if kind == "real":
            with real_db.begin() as conn:
                conn.execute(text("DELETE FROM frontline_snapshots WHERE id = ANY(:ids)"),
                             {"ids": list(ids.values())})

    assert survivors == {"at_cutoff", "recent", "latest"}
    assert deleted >= 2   # the real table may also hold older rows from other tests


def test_prune_with_nothing_old_enough_deletes_nothing():
    adapter = FakeFrontlineAdapter()
    adapter.save_snapshot(1, datetime(2026, 10, 1, tzinfo=timezone.utc), None, _fc("occupied"))
    assert adapter.prune_older_than(60) == 0
    assert FakeFrontlineAdapter().prune_older_than(60) == 0
