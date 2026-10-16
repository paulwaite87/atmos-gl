#!/usr/bin/env python3
"""Guard against InternetOutagesAdapter Real/Fake drift (the pattern
test_tsunami_adapter_real_vs_fake.py follows): the same contract scenarios run against the
SQLAlchemy adapter (a throwaway postgis container migrated with `alembic upgrade head`) and
FakeInternetOutagesAdapter. Everything is asserted through the adapter's public methods,
never by querying tables.
"""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from terrascope.db.internet_outages_adapter import (
    FakeInternetOutagesAdapter,
    InternetOutagesAdapter,
)


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return InternetOutagesAdapter(), patch(
            "terrascope.db.internet_outages_adapter.Session", TestSession
        )
    return FakeInternetOutagesAdapter(), contextlib.nullcontext()


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def make_event(code="ZA", **over):
    event = {
        "country_code": code,
        "country_name": "Testland",
        "signal": "bgp",
        "start": NOW - timedelta(hours=5),
        "end": NOW,
        "ongoing": True,
        "score": 1234.5,
    }
    event.update(over)
    return event


def countries(adapter, **kwargs):
    return json.loads(adapter.get_countries_as_json(**kwargs))["countries"]


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_event_is_served_under_its_country_with_its_signal(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event("ZA"))
        served = countries(adapter)["ZA"]

    assert served["name"] == "Testland"
    assert served["severity"] == 1
    assert served["ongoing"] is True
    [signal] = served["signals"]
    assert signal["signal"] == "bgp"
    assert signal["ongoing"] is True
    assert signal["duration_s"] >= 5 * 3600
    assert signal["start"].endswith("Z")


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_severity_counts_distinct_signals_capped_at_three(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        for signal in ("bgp", "ping-slash24", "gtr", "merit-nt"):
            adapter.upsert_event(make_event("ZB", signal=signal))
        adapter.upsert_event(make_event("ZC", signal="bgp"))
        adapter.upsert_event(
            make_event("ZC", signal="bgp", start=NOW - timedelta(hours=2))
        )
        served = countries(adapter)

    assert served["ZB"]["severity"] == 3
    assert served["ZC"]["severity"] == 1
    assert len(served["ZC"]["signals"]) == 2


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_upserting_the_same_event_updates_it_in_place(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    start = NOW - timedelta(hours=4)
    with ctx:
        adapter.upsert_event(
            make_event("ZD", start=start, end=NOW - timedelta(hours=1), ongoing=False)
        )
        adapter.upsert_event(make_event("ZD", start=start, end=NOW, ongoing=True))
        served = countries(adapter)["ZD"]

    assert len(served["signals"]) == 1
    assert served["ongoing"] is True


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_ended_event_shows_only_within_the_grace_period(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event("G1"))
        adapter.upsert_event(
            make_event(
                "G2",
                start=NOW - timedelta(hours=14),
                end=NOW - timedelta(hours=12),
                ongoing=False,
            )
        )
        adapter.upsert_event(
            make_event(
                "G3",
                start=NOW - timedelta(days=4),
                end=NOW - timedelta(days=3),
                ongoing=False,
            )
        )
        mine = {"G1", "G2", "G3"}
        default = set(countries(adapter)) & mine
        none = set(countries(adapter, grace_days=0)) & mine
        wide = set(countries(adapter, grace_days=5)) & mine

    assert default == {"G1", "G2"}
    assert none == {"G1"}
    assert wide == {"G1", "G2", "G3"}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_pruning_deletes_events_that_ended_long_ago_but_never_ongoing_ones(
    kind, real_db
):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.upsert_event(make_event("P1"))
        adapter.upsert_event(
            make_event(
                "P2",
                start=NOW - timedelta(days=30),
                end=NOW - timedelta(days=29),
                ongoing=False,
            )
        )
        adapter.upsert_event(
            make_event(
                "P3",
                start=NOW - timedelta(days=2),
                end=NOW - timedelta(days=1),
                ongoing=False,
            )
        )
        deleted = adapter.prune_expired(7)
        remaining = set(countries(adapter, grace_days=1000)) & {"P1", "P2", "P3"}

    assert deleted >= 1
    assert remaining == {"P1", "P3"}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_closing_unseen_ongoing_events_ends_only_the_ones_not_listed(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    kept = make_event("C1")
    dropped = make_event("C2")
    with ctx:
        adapter.upsert_event(kept)
        adapter.upsert_event(dropped)
        seen = {("C1", "bgp", kept["start"])}
        # The real database is shared across tests, so other tests' ongoing rows are closed
        # too; only this test's two countries are asserted on.
        closed = adapter.close_unseen_ongoing(seen)
        served = countries(adapter)

    assert closed >= 1
    assert served["C1"]["ongoing"] is True
    assert served["C2"]["ongoing"] is False
