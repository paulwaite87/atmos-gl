#!/usr/bin/env python3
"""Tests for WorldEventsCollector -- GDELT Event Database 2.0 ingestion, filtered to a
curated CAMEO code allowlist (see collectors/world_events.py's module docstring).
Covers: category classification (including correct exclusions), the min_mentions
noise floor, and slot-based coverage: has_new_data(), gap-fill anywhere in the
backfill window, 404/transient-failure handling, and the per-cycle cap (see the
collector module's docstring).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.collectors.world_events import (
    _MAX_FILES_PER_CYCLE,
    _MAX_PREVIEWS_PER_CYCLE,
    ExportFileMissing,
    WorldEventsCollector,
    _classify,
    _expected_slots,
    _floor_to_slot,
    _parse_export_rows,
)
from atmos_gl.db.world_event_adapter import FakeWorldEventAdapter
from atmos_gl.lib.article_preview import STATUS_FAILED, STATUS_OK, STATUS_RETRY, ArticlePreview

_BASE_URL = "http://data.gdeltproject.org/gdeltv2"


@pytest.fixture(autouse=True)
def _no_real_article_fetches():
    """collect() ends with an article-preview pass; never let a test hit the network.
    Tests that care about previews re-patch fetch_article_preview themselves."""
    with patch(
        "atmos_gl.collectors.world_events.fetch_article_preview",
        return_value=ArticlePreview(STATUS_FAILED),
    ) as stub:
        yield stub


def make_collector(settings=None):
    c = WorldEventsCollector.__new__(WorldEventsCollector)
    c.settings = settings if settings is not None else {}
    c.world_event_adapter = MagicMock()
    c._etag_cache = {}
    c.datasource_url = MagicMock(return_value=_BASE_URL)
    return c


def _row(
    event_code="183", actor1="", actor2="", lat="10.0", lon="20.0",
    num_mentions="20", num_sources="2", goldstein="1.0", avg_tone="0.5",
    date_added="20260821120000", source_url="http://example.com/a",
    global_event_id="123", action_geo_fullname="Somewhere",
    # Non-blank by default so tests unrelated to actor-vetting keep classifying as
    # before -- see test_classify_conflict_categories_require_a_state_or_group_actor
    # for the case that deliberately blanks these out.
    actor1_country="USA", actor2_country="", actor1_type1="", actor2_type1="",
):
    fields = [""] * 61
    fields[0] = global_event_id
    fields[6] = actor1
    fields[7] = actor1_country
    fields[12] = actor1_type1
    fields[16] = actor2
    fields[17] = actor2_country
    fields[22] = actor2_type1
    fields[26] = event_code
    fields[30] = goldstein
    fields[31] = num_mentions
    fields[32] = num_sources
    fields[34] = avg_tone
    fields[52] = action_geo_fullname
    fields[56] = lat
    fields[57] = lon
    fields[59] = date_added
    fields[60] = source_url
    return "\t".join(fields)


class _FakeResponse:
    def __init__(self, text="", lines=None):
        self.text = text
        self._lines = lines or []

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


# ---- _classify ---------------------------------------------------------------------

def test_classify_explosion_codes():
    assert _classify("183", None, None, True) == "explosion"
    assert _classify("1831", None, None, True) == "explosion"


def test_classify_warfare_codes():
    assert _classify("193", None, None, True) == "warfare"


def test_classify_targeted_violence_codes():
    assert _classify("202", None, None, True) == "targeted_violence"
    assert _classify("2041", None, None, True) == "targeted_violence"


def test_classify_conflict_categories_require_a_state_or_group_actor():
    """The false-positive fix: GDELT's NLP coder can match figurative "battle"/
    "fight"/"war" language (e.g. a film-casting article about "the battle to become
    the next James Bond") into these CAMEO bands. A real conflict's actors resolve to
    a country/military/group entity in CAMEO's dictionaries; an unresolvable proper
    noun doesn't, so has_state_actor=False drops it instead of miscategorizing it."""
    assert _classify("183", "Some Actor", None, False) is None
    assert _classify("193", "Some Actor", None, False) is None
    assert _classify("202", "Some Actor", None, False) is None


def test_classify_diplomacy_requires_both_root_code_and_org_match():
    assert _classify("046", "NATO", "United Kingdom") == "diplomacy"
    # Root-04 event between two ordinary actors, no curated org named -- too generic
    # to count as a World Event (see module docstring).
    assert _classify("046", "France", "Germany") is None


def test_classify_diplomacy_org_match_is_case_insensitive():
    assert _classify("042", "nato secretary general", None) == "diplomacy"


def test_classify_unmatched_code_returns_none():
    assert _classify("010", "Some Actor", None) is None


# ---- _parse_export_rows -------------------------------------------------------------

def test_parse_export_rows_extracts_classified_rows_with_expected_fields():
    csv_text = _row(
        event_code="183", global_event_id="999", lat="51.5", lon="-0.1",
        num_mentions="15", num_sources="3", goldstein="-5.0", avg_tone="-2.3",
        source_url="http://news.example/a", action_geo_fullname="London, UK",
        date_added="20260821123000",
    )
    rows = _parse_export_rows(csv_text)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "999"
    assert row["category"] == "explosion"
    assert row["event_code"] == "183"
    assert row["lat"] == 51.5 and row["lon"] == -0.1
    assert row["num_mentions"] == 15
    assert row["num_sources"] == 3
    assert row["goldstein_scale"] == -5.0
    assert row["avg_tone"] == -2.3
    assert row["source_url"] == "http://news.example/a"
    assert row["action_geo_full_name"] == "London, UK"
    assert row["event_date"] == datetime(2026, 8, 21, 12, 30, tzinfo=timezone.utc).isoformat()


def test_parse_export_rows_excludes_unclassified_and_missing_coords():
    csv_text = "\n".join([
        _row(event_code="010"),           # not in any category allowlist
        _row(event_code="183", lat="", lon=""),  # classified but no coordinates
        _row(event_code="183"),           # valid
    ])
    rows = _parse_export_rows(csv_text)
    assert len(rows) == 1


def test_parse_export_rows_excludes_conflict_code_with_unresolved_actor():
    """Regression test for the GDELT false-positive fix: a warfare-coded event whose
    actors don't resolve to any CAMEO country/military/group entity (Actor*CountryCode
    and Type1-3Code all blank) is dropped, not upserted as "Conflict or War"."""
    csv_text = _row(event_code="193", actor1_country="", actor2_country="")
    rows = _parse_export_rows(csv_text)
    assert rows == []


def test_parse_export_rows_skips_malformed_lines_without_raising():
    csv_text = "\n".join([
        _row(event_code="183", num_mentions="not-a-number"),
        _row(event_code="183"),  # valid
        "too\tfew\tcolumns",
    ])
    rows = _parse_export_rows(csv_text)
    assert len(rows) == 1


def test_parse_export_rows_does_not_apply_a_mentions_floor():
    """min_mentions is applied by the caller (collect()/backfill), not baked into
    parsing -- a row with zero mentions still comes back classified."""
    csv_text = _row(event_code="183", num_mentions="0")
    rows = _parse_export_rows(csv_text)
    assert len(rows) == 1
    assert rows[0]["num_mentions"] == 0


# ---- slot-based coverage: has_new_data / collect / backfill ---------------------
# These run against FakeWorldEventAdapter (not a MagicMock) so processed-slot
# bookkeeping behaves like the real table across successive collect() calls.

def make_slot_collector(settings=None):
    c = make_collector(settings if settings is not None else {"backfill_days": 1})
    c.world_event_adapter = FakeWorldEventAdapter()
    return c


def _latest():
    return _floor_to_slot(datetime.now(timezone.utc))


def _lastupdate_for(slot):
    return _FakeResponse(
        text=f"123 abc {_BASE_URL}/{slot.strftime('%Y%m%d%H%M%S')}.export.CSV.zip"
    )


def _window_slots(c, latest):
    window_start = datetime.now(timezone.utc) - timedelta(days=c.settings["backfill_days"])
    return _expected_slots(window_start, latest)


def _mark_all(c, slots):
    for slot in slots:
        c.world_event_adapter.mark_export_processed(slot, "ok", 0)


def _slot_of(url):
    return datetime.strptime(url.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )


def test_has_new_data_true_when_the_newest_slot_is_unprocessed():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest)[1:])
    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        assert c.has_new_data() is True


def test_has_new_data_false_when_every_slot_in_the_window_is_processed():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest))
    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        assert c.has_new_data() is False


def test_has_new_data_defaults_true_on_fetch_failure():
    c = make_slot_collector()
    with patch.object(CollectorBase, "_get", return_value=None):
        assert c.has_new_data() is True


def test_collect_upserts_rows_at_or_above_the_mentions_floor_and_records_the_slot():
    c = make_slot_collector({"min_mentions": 10, "backfill_days": 1})
    latest = _latest()
    _mark_all(c, _window_slots(c, latest)[1:])
    csv_text = "\n".join([
        _row(event_code="183", global_event_id="1", num_mentions="20"),
        _row(event_code="193", global_event_id="2", num_mentions="5"),  # below floor
    ])

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv", return_value=csv_text):
        c.collect()

    assert set(c.world_event_adapter._events) == {"1"}
    assert c.world_event_adapter._export_files[latest] == {"status": "ok", "row_count": 1}


def test_collect_skips_when_lastupdate_has_no_export_entry():
    c = make_slot_collector()
    with patch.object(CollectorBase, "_get", return_value=_FakeResponse(text="no export file here")), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv") as mock_fetch:
        c.collect()
    mock_fetch.assert_not_called()


def test_collect_fills_a_gap_in_the_middle_of_the_window():
    # The original bug: coverage was judged only by the OLDEST stored event, so a
    # collector outage mid-window was never backfilled. Every slot is processed
    # except two in the middle -- exactly those two must be fetched.
    c = make_slot_collector()
    latest = _latest()
    slots = _window_slots(c, latest)
    gap = {slots[40], slots[41]}
    _mark_all(c, [s for s in slots if s not in gap])

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv", return_value="") as mock_fetch:
        c.collect()

    assert {_slot_of(call.args[0]) for call in mock_fetch.call_args_list} == gap
    # A file yielding zero curated events still counts as covered.
    assert all(c.world_event_adapter._export_files[s]["status"] == "ok" for s in gap)


def test_collect_records_a_404_slot_as_missing_and_never_refetches_it():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest)[1:])

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv",
               side_effect=ExportFileMissing("gone")) as mock_fetch:
        c.collect()
        c.collect()

    assert mock_fetch.call_count == 1
    assert c.world_event_adapter._export_files[latest]["status"] == "missing"


def test_collect_leaves_a_transiently_failed_slot_pending_without_blocking_the_rest():
    c = make_slot_collector()
    latest = _latest()
    slots = _window_slots(c, latest)
    flaky, fine = slots[0], slots[1]
    _mark_all(c, slots[2:])

    def fake_fetch(url):
        if _slot_of(url) == flaky:
            raise requests.ConnectionError("outage")
        return _row(event_code="183", global_event_id="ok", num_mentions="20")

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv", side_effect=fake_fetch):
        c.collect()  # must not raise

    assert flaky not in c.world_event_adapter._export_files
    assert c.world_event_adapter._export_files[fine]["status"] == "ok"

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv",
               return_value="") as mock_fetch:
        c.collect()
    assert [_slot_of(call.args[0]) for call in mock_fetch.call_args_list] == [flaky]


def test_collect_leaves_the_slot_pending_when_the_upsert_fails():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest)[1:])
    c.world_event_adapter.upsert_events = MagicMock(return_value=False)

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv",
               return_value=_row(event_code="183", num_mentions="20")):
        c.collect()

    assert latest not in c.world_event_adapter._export_files


def test_collect_caps_files_per_cycle_newest_first():
    c = make_slot_collector()  # empty table: the whole 1-day window (97 slots) is pending
    latest = _latest()
    slots = _window_slots(c, latest)
    assert len(slots) > _MAX_FILES_PER_CYCLE

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv", return_value="") as mock_fetch:
        c.collect()

    fetched = [_slot_of(call.args[0]) for call in mock_fetch.call_args_list]
    assert fetched == slots[:_MAX_FILES_PER_CYCLE]


def test_collect_never_requests_a_slot_newer_than_lastupdate():
    c = make_slot_collector()
    latest = _latest() - timedelta(hours=1)  # GDELT running an hour behind
    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)), \
         patch("atmos_gl.collectors.world_events._fetch_export_csv", return_value="") as mock_fetch:
        c.collect()
    assert max(_slot_of(call.args[0]) for call in mock_fetch.call_args_list) == latest


def test_collect_prunes_slot_records_older_than_the_window():
    c = make_slot_collector()
    latest = _latest()
    stale = latest - timedelta(days=5)
    c.world_event_adapter.mark_export_processed(stale, "ok", 3)
    _mark_all(c, _window_slots(c, latest))

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        c.collect()

    assert stale not in c.world_event_adapter._export_files



# ---- article previews ---------------------------------------------------------------

def _seed_event(c, event_id, url, hours_ago=1):
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    c.world_event_adapter.upsert_events([{
        "id": event_id, "category": "warfare", "event_code": "190",
        "actor1_name": None, "actor2_name": None, "action_geo_full_name": None,
        "lat": 1.0, "lon": 2.0, "event_date": when.isoformat(),
        "num_mentions": 20, "num_sources": 1, "goldstein_scale": None,
        "avg_tone": None, "source_url": url,
    }])


def test_has_new_data_true_when_only_article_previews_are_pending():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest))
    _seed_event(c, "e1", "https://news.example/a")
    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        assert c.has_new_data() is True


def test_collect_stores_a_preview_per_distinct_url(_no_real_article_fetches):
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest))
    _seed_event(c, "e1", "https://news.example/a")
    _seed_event(c, "e2", "https://news.example/a")  # same article, second event
    _seed_event(c, "e3", "https://news.example/b")
    previews = {
        "https://news.example/a": ArticlePreview(STATUS_OK, 200, "Headline A", "Lede A."),
        "https://news.example/b": ArticlePreview(STATUS_RETRY, 503),
    }
    _no_real_article_fetches.side_effect = previews.get

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        c.collect()

    fetched = sorted(call.args[0] for call in _no_real_article_fetches.call_args_list)
    assert fetched == ["https://news.example/a", "https://news.example/b"]
    articles = c.world_event_adapter._articles
    assert articles["https://news.example/a"]["headline"] == "Headline A"
    assert articles["https://news.example/a"]["summary"] == "Lede A."
    assert articles["https://news.example/b"]["status"] == STATUS_RETRY


def test_collect_caps_previews_per_cycle_newest_events_first(_no_real_article_fetches):
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest))
    n = _MAX_PREVIEWS_PER_CYCLE + 5
    for i in range(n):  # i=0 is the newest event
        _seed_event(c, f"e{i}", f"https://news.example/{i}", hours_ago=1 + i * 0.1)

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        c.collect()

    fetched = {call.args[0] for call in _no_real_article_fetches.call_args_list}
    assert fetched == {f"https://news.example/{i}" for i in range(_MAX_PREVIEWS_PER_CYCLE)}


def test_a_failing_preview_pass_does_not_fail_the_cycle():
    c = make_slot_collector()
    latest = _latest()
    _mark_all(c, _window_slots(c, latest))
    _seed_event(c, "e1", "https://news.example/a")
    c.world_event_adapter.save_article_previews = MagicMock(side_effect=RuntimeError("db down"))

    with patch.object(CollectorBase, "_get", return_value=_lastupdate_for(latest)):
        c.collect()  # must not raise
