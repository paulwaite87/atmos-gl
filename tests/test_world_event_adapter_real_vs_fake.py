#!/usr/bin/env python3
"""Guard against WorldEventAdapter Real/Fake drift, mirroring
test_fire_adapter_real_vs_fake.py: FakeWorldEventAdapter hand-reimplements
WorldEventAdapter's on-conflict SQL and expiry filter independently, so if they ever
diverge, nothing else would catch it. Unlike FireAdapter (whose lat/lon/geom is
immutable on conflict), every column here refreshes on a re-ingested GLOBALEVENTID --
GDELT's own fields for a given event don't change after the fact, so there's no
"protect the original coordinate" concern a backfill re-covering an already-collected
window needs to respect.
"""
import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.world_event_adapter import WorldEventAdapter, FakeWorldEventAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return WorldEventAdapter(), patch("atmos_gl.db.world_event_adapter.Session", TestSession)
    return FakeWorldEventAdapter(), contextlib.nullcontext()


def _row(adapter, event_id, real_db):
    if isinstance(adapter, FakeWorldEventAdapter):
        e = adapter._events[event_id]
        return {"category": e["category"], "num_mentions": e["num_mentions"], "lat": e["lat"], "lon": e["lon"]}
    with real_db.connect() as conn:
        result = conn.execute(
            text("SELECT category, num_mentions, lat, lon FROM world_events WHERE id = :id"),
            {"id": event_id},
        ).mappings().one()
        return dict(result)


def _event_row(
    event_id, category, lat, lon, event_date_iso, num_mentions=20, event_code="183",
    avg_tone=None,
):
    return {
        "id": event_id, "category": category, "event_code": event_code,
        "actor1_name": None, "actor2_name": None, "action_geo_full_name": None,
        "lat": lat, "lon": lon, "event_date": event_date_iso,
        "num_mentions": num_mentions, "num_sources": 1,
        "goldstein_scale": None, "avg_tone": avg_tone, "source_url": None,
    }


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_every_column_refreshes_on_conflict(kind, real_db):
    event_id = f"we-update-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)
    now_iso = datetime.now(timezone.utc).isoformat()

    with ctx:
        adapter.upsert_events([_event_row(event_id, "warfare", 10.0, 20.0, now_iso, num_mentions=5)])
        adapter.upsert_events([_event_row(event_id, "explosion", 30.0, 40.0, now_iso, num_mentions=99)])
        row = _row(adapter, event_id, real_db)

    assert row["category"] == "explosion"
    assert row["num_mentions"] == 99
    assert row["lat"] == pytest.approx(30.0)
    assert row["lon"] == pytest.approx(40.0)


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_rows_missing_coordinates_are_never_stored(kind, real_db):
    event_id = f"we-nocoord-{kind}"
    adapter, ctx = _make_adapter(kind, real_db)
    now_iso = datetime.now(timezone.utc).isoformat()
    row = _event_row(event_id, "warfare", None, None, now_iso)

    with ctx:
        adapter.upsert_events([row])
        geojson = json.loads(adapter.get_events_as_geojson(expiry_days=36500))

    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert event_id not in ids


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_events_as_geojson_expiry_filter_matches_between_real_and_fake(kind, real_db):
    suffix = kind
    adapter, ctx = _make_adapter(kind, real_db)
    now = datetime.now(timezone.utc)
    recent_iso = now.isoformat()
    old_iso = (now - timedelta(days=30)).isoformat()

    with ctx:
        adapter.upsert_events([
            _event_row(f"recent-{suffix}", "warfare", 10.0, 20.0, recent_iso),
            _event_row(f"old-{suffix}", "warfare", 10.0, 20.0, old_iso),
        ])
        geojson = json.loads(adapter.get_events_as_geojson(expiry_days=7))

    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert f"recent-{suffix}" in ids
    assert f"old-{suffix}" not in ids


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_delete_expired_prunes_only_rows_older_than_expiry_days(kind, real_db):
    suffix = kind
    adapter, ctx = _make_adapter(kind, real_db)
    now = datetime.now(timezone.utc)

    with ctx:
        adapter.upsert_events([
            _event_row(f"keep-{suffix}", "warfare", 10.0, 20.0, now.isoformat()),
            _event_row(f"prune-{suffix}", "warfare", 10.0, 20.0, (now - timedelta(days=10)).isoformat()),
        ])
        deleted = adapter.delete_expired(expiry_days=7)
        geojson = json.loads(adapter.get_events_as_geojson(expiry_days=36500))

    # >=1 rather than an exact count: the real_db fixture's table is shared across
    # this module's tests, so an earlier test's own aged-out row(s) may also get
    # swept up here -- what matters is that THIS test's prune row is gone and its
    # keep row survived, not the total row count deleted.
    assert deleted >= 1
    ids = {f["properties"]["id"] for f in geojson["features"]}
    assert f"keep-{suffix}" in ids
    assert f"prune-{suffix}" not in ids


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_get_events_as_geojson_tone_filter_matches_between_real_and_fake(kind, real_db):
    # max_conflict_tone drops conflict-category events whose coverage reads MORE
    # positively than the threshold (GDELT's figurative "battle"/"fight" false
    # positives), but never diplomacy (whose tone is naturally either sign) and never
    # an event with no tone at all (nothing to judge it by).
    suffix = kind
    adapter, ctx = _make_adapter(kind, real_db)
    now_iso = datetime.now(timezone.utc).isoformat()

    with ctx:
        adapter.upsert_events([
            _event_row(f"neg-war-{suffix}", "warfare", 10.0, 20.0, now_iso, avg_tone=-4.0),
            _event_row(f"at-war-{suffix}", "warfare", 10.0, 20.0, now_iso, avg_tone=0.0),
            _event_row(f"pos-war-{suffix}", "warfare", 10.0, 20.0, now_iso, avg_tone=3.5),
            _event_row(f"pos-boom-{suffix}", "explosion", 10.0, 20.0, now_iso, avg_tone=0.1),
            _event_row(f"pos-tv-{suffix}", "targeted_violence", 10.0, 20.0, now_iso, avg_tone=2.0),
            _event_row(f"pos-dip-{suffix}", "diplomacy", 10.0, 20.0, now_iso, avg_tone=5.0),
            _event_row(f"null-war-{suffix}", "warfare", 10.0, 20.0, now_iso, avg_tone=None),
        ])
        filtered = json.loads(adapter.get_events_as_geojson(expiry_days=7, max_conflict_tone=0.0))
        unfiltered = json.loads(adapter.get_events_as_geojson(expiry_days=7))

    ids = {f["properties"]["id"] for f in filtered["features"]}
    assert {f"neg-war-{suffix}", f"at-war-{suffix}", f"pos-dip-{suffix}", f"null-war-{suffix}"} <= ids
    assert not {f"pos-war-{suffix}", f"pos-boom-{suffix}", f"pos-tv-{suffix}"} & ids

    all_ids = {f["properties"]["id"] for f in unfiltered["features"]}
    assert {f"pos-war-{suffix}", f"pos-boom-{suffix}", f"pos-tv-{suffix}"} <= all_ids


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_export_slot_bookkeeping_matches_between_real_and_fake(kind, real_db):
    # Slots are offset per kind (and far from "now") so the real and fake runs, which
    # share one session-scoped table on the real side, can't see each other's rows.
    base = datetime(2001 if kind == "real" else 2002, 1, 1, tzinfo=timezone.utc)
    old, mid, new = base, base + timedelta(minutes=15), base + timedelta(minutes=30)
    adapter, ctx = _make_adapter(kind, real_db)

    with ctx:
        adapter.mark_export_processed(old, "ok", 4)
        adapter.mark_export_processed(mid, "missing")
        adapter.mark_export_processed(new, "ok", 1)
        adapter.mark_export_processed(new, "ok", 2)  # re-marking is idempotent
        assert adapter.processed_export_slots(mid) == {mid, new}

        pruned = adapter.prune_export_slots(mid)
        assert adapter.processed_export_slots(base - timedelta(days=1)) == {mid, new}

    assert pruned >= 1


def _age_article(adapter, url, real_db, hours):
    """Backdates an article's last attempt so its retry becomes due."""
    if isinstance(adapter, FakeWorldEventAdapter):
        adapter._articles[url]["fetched_at"] -= timedelta(hours=hours)
        return
    with real_db.begin() as conn:
        conn.execute(
            text("UPDATE world_event_articles SET fetched_at = fetched_at - make_interval(hours => :h) WHERE url = :u"),
            {"h": hours, "u": url},
        )


def _preview(url, status, headline=None, summary=None, http_status=200):
    return {"url": url, "status": status, "http_status": http_status,
            "headline": headline, "summary": summary}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_urls_needing_preview_matches_between_real_and_fake(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    now = datetime.now(timezone.utc)
    u = {name: f"https://news.example/{kind}/{name}" for name in
         ("new", "older", "ok", "failed", "retry_recent", "retry_due", "retry_exhausted", "stale")}

    def ev(event_id, url, hours_ago):
        row = _event_row(f"{kind}-{event_id}", "warfare", 10.0, 20.0,
                         (now - timedelta(hours=hours_ago)).isoformat())
        row["source_url"] = url
        return row

    with ctx:
        adapter.upsert_events([
            ev("new", u["new"], 1),
            ev("new-dup", u["new"], 2),  # same article coded as two events
            ev("older", u["older"], 5),
            ev("ok", u["ok"], 1),
            ev("failed", u["failed"], 1),
            ev("retry_recent", u["retry_recent"], 1),
            ev("retry_due", u["retry_due"], 1),
            ev("retry_exhausted", u["retry_exhausted"], 1),
            ev("stale", u["stale"], 24 * 10),  # outside `since`
        ])
        adapter.save_article_previews([
            _preview(u["ok"], "ok", "H"),
            _preview(u["failed"], "failed", http_status=403),
            _preview(u["retry_recent"], "retry", http_status=429),
            _preview(u["retry_due"], "retry", http_status=503),
        ])
        for _ in range(3):
            adapter.save_article_previews([_preview(u["retry_exhausted"], "retry", http_status=500)])
        _age_article(adapter, u["retry_due"], real_db, hours=2)
        _age_article(adapter, u["retry_exhausted"], real_db, hours=2)

        needed = adapter.urls_needing_preview(now - timedelta(days=3), limit=100)

    # The real table is shared across this module's tests, so look only at this run's URLs.
    mine = [url for url in needed if f"/{kind}/" in url]
    assert set(mine) == {u["new"], u["older"], u["retry_due"]}
    assert mine.index(u["older"]) > mine.index(u["new"])  # most recent event first


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_geojson_carries_headline_and_summary_only_for_ok_articles(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    now_iso = datetime.now(timezone.utc).isoformat()
    ok_url, retry_url = f"https://news.example/{kind}/geo-ok", f"https://news.example/{kind}/geo-retry"

    def ev(event_id, url):
        row = _event_row(f"geo-{kind}-{event_id}", "warfare", 10.0, 20.0, now_iso)
        row["source_url"] = url
        return row

    with ctx:
        adapter.upsert_events([ev("ok", ok_url), ev("retry", retry_url), ev("none", None)])
        adapter.save_article_previews([
            _preview(ok_url, "ok", "Strikes hit Kabul", "Twenty-two killed."),
            _preview(retry_url, "retry", "should not show", http_status=503),
        ])
        geojson = json.loads(adapter.get_events_as_geojson(expiry_days=7))

    props = {f["properties"]["id"]: f["properties"] for f in geojson["features"]}
    assert props[f"geo-{kind}-ok"]["headline"] == "Strikes hit Kabul"
    assert props[f"geo-{kind}-ok"]["summary"] == "Twenty-two killed."
    assert props[f"geo-{kind}-retry"]["headline"] is None
    assert props[f"geo-{kind}-none"]["headline"] is None


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_prune_orphaned_articles_matches_between_real_and_fake(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    now = datetime.now(timezone.utc)
    kept_url = f"https://news.example/{kind}/orphan-kept"
    shared_url = f"https://news.example/{kind}/orphan-shared"
    gone_url = f"https://news.example/{kind}/orphan-gone"

    def ev(event_id, url, days_ago):  # ids must fit world_events.id's varchar(20)
        row = _event_row(f"o-{kind}-{event_id}", "warfare", 10.0, 20.0,
                         (now - timedelta(days=days_ago)).isoformat())
        row["source_url"] = url
        return row

    with ctx:
        adapter.upsert_events([
            ev("kept", kept_url, 1),
            ev("shared-old", shared_url, 30),  # one old + one recent event, same article
            ev("shared-new", shared_url, 1),
            ev("gone", gone_url, 30),
        ])
        adapter.save_article_previews([
            _preview(kept_url, "ok", "Kept"),
            _preview(shared_url, "ok", "Shared"),
            _preview(gone_url, "ok", "Gone"),
        ])
        adapter.delete_expired(expiry_days=14)
        pruned = adapter.prune_orphaned_articles()
        geojson = json.loads(adapter.get_events_as_geojson(expiry_days=36500))

    headlines = {f["properties"]["id"]: f["properties"]["headline"] for f in geojson["features"]}
    assert headlines[f"o-{kind}-kept"] == "Kept"
    assert headlines[f"o-{kind}-shared-new"] == "Shared"  # still referenced
    assert f"o-{kind}-gone" not in headlines
    assert pruned >= 1
    if kind == "fake":
        assert gone_url not in adapter._articles
    else:
        with real_db.connect() as conn:
            remaining = conn.execute(
                text("SELECT url FROM world_event_articles WHERE url = ANY(:urls)"),
                {"urls": [kept_url, shared_url, gone_url]},
            ).scalars().all()
        assert set(remaining) == {kept_url, shared_url}
