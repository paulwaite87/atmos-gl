#!/usr/bin/env python3
"""Ingest-to-HTTP tests for the GDACS Alerts layer: GdacsCollector.collect() runs against
recorded GDACS list/footprint responses (tests/fixtures/gdacs/, trimmed real feed
output) with CollectorBase._get patched, fills the in-memory FakeGdacsAdapter, and every
assertion is on what the layer's HTTP routes then serve -- never on adapter internals.
Prior art: test_quakes_collector.py (collector) + test_quakes_route.py (route seam)."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from terrascope.api import app
from terrascope.collectors.base import CollectorBase
from terrascope.collectors.gdacs import GdacsCollector
from terrascope.db.gdacs_adapter import FakeGdacsAdapter
from terrascope.routes.gdacs import get_gdacs_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "gdacs"
LIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def make_collector(**settings):
    c = GdacsCollector.__new__(GdacsCollector)
    c.settings = {"min_alert": "Orange", "alert_overrides": {}, **settings}
    c.gdacs_adapter = FakeGdacsAdapter()
    c.datasource_url = MagicMock(return_value=LIST_URL)
    return c


FOOTPRINT_RE = re.compile(r"eventtype=(\w+)&eventid=(\d+)")


def run_collect(c, pages, footprints=None, footprint_calls=None, list_params=None):
    """Run collect() with the list endpoint serving `pages` (page 1, 2, ...) and the
    per-event footprint endpoint serving `footprints` ({(type, id): payload}); a missing
    footprint behaves like a failed download. Footprint requests are appended to
    `footprint_calls` when given."""
    footprints = footprints or {}

    def fake_get(url, **kwargs):
        m = FOOTPRINT_RE.search(url)
        if m:
            key = (m.group(1), int(m.group(2)))
            if footprint_calls is not None:
                footprint_calls.append(key)
            payload = footprints.get(key)
            return FakeResponse(payload) if payload is not None else None
        if list_params is not None:
            list_params.append(dict(kwargs.get("params", {})))
        page = int(kwargs.get("params", {}).get("pagenumber", 1))
        return FakeResponse(pages[page - 1]) if page <= len(pages) else None

    with patch.object(CollectorBase, "_get", side_effect=fake_get):
        c.collect()
    app.dependency_overrides[get_gdacs_adapter] = lambda: c.gdacs_adapter


def points_served_after_collect(client, c, pages, **kwargs):
    run_collect(c, pages, **kwargs)
    resp = client.get("/api/gdacs/geojson")
    assert resp.status_code == 200
    return resp.json()


def footprint_classes(client, event_type, event_id):
    resp = client.get(f"/api/gdacs/{event_type}/{event_id}/footprint")
    assert resp.status_code == 200
    return [f["properties"]["feature_class"] for f in resp.json()["features"]]


def test_a_red_cyclone_is_served_with_its_type_alert_level_and_severity(client):
    c = make_collector()

    body = points_served_after_collect(client, c, [load_fixture("events_page1.json")])

    by_id = {f["properties"]["event_id"]: f for f in body["features"]}
    champi = by_id[1001338]
    assert champi["properties"]["event_type"] == "TC"
    assert champi["properties"]["alert_level"] == "Red"
    assert champi["properties"]["country"] == "Guam"
    assert champi["properties"]["severity_text"].startswith("Tropical Storm")
    assert champi["geometry"]["type"] == "Point"
    # GDACS sends booleans as the strings "true"/"false"; the route serves a real bool.
    assert champi["properties"]["is_current"] is True
    assert champi["properties"]["report_url"].startswith(
        "https://www.gdacs.org/report.aspx?eventid=1001338"
    )
    assert champi["properties"]["from_date"] == "2026-10-14T12:00:00Z"
    assert champi["properties"]["name"] == "Tropical Cyclone CHAMPI-26"


def served_ids(body):
    return {f["properties"]["event_id"] for f in body["features"]}


def test_the_minimum_alert_level_applies_except_where_a_type_override_lowers_it(client):
    # Template defaults: Orange minimum, but floods/droughts/cyclones allowed down to Green.
    c = make_collector(
        min_alert="Orange",
        alert_overrides={"FL": "Green", "DR": "Green", "TC": "Green"},
    )

    body = points_served_after_collect(client, c, [load_fixture("events_page1.json")])

    # Fixture: TC Red 1001338, TC Orange 1001335, EQ Green 1571932, FL Green 1103888,
    # WF Green 1032639. The Green earthquake and wildfire fall below Orange; the Green
    # flood is let through by its FL override.
    assert served_ids(body) == {1001338, 1001335, 1103888}


def test_without_overrides_the_minimum_alert_level_hides_everything_below_it(client):
    c = make_collector(min_alert="Orange", alert_overrides={})

    body = points_served_after_collect(client, c, [load_fixture("events_page1.json")])

    assert served_ids(body) == {1001338, 1001335}


def test_only_the_configured_hazard_types_are_collected(client):
    c = make_collector(min_alert="Green", types=["EQ", "FL"])

    body = points_served_after_collect(client, c, [load_fixture("events_page1.json")])

    # Fixture at Green minimum would otherwise also include both cyclones and the wildfire.
    assert served_ids(body) == {1571932, 1103888}


def padded_full_page():
    """A full 100-feature GDACS page (the API's fixed page size): the real fixture events
    plus filler Green earthquakes (hidden by the default filter) so the collector sees a
    full page and must ask for the next one."""
    page = load_fixture("events_page1.json")
    template = next(f for f in page["features"] if f["properties"]["eventtype"] == "EQ")
    while len(page["features"]) < 100:
        filler = json.loads(json.dumps(template))
        filler["properties"]["eventid"] = 9_000_000 + len(page["features"])
        page["features"].append(filler)
    return page


def test_collection_follows_pages_until_a_short_page(client):
    page2 = load_fixture("events_page1.json")
    # Page 2 (short): one real Red cyclone with a distinct id stands in for "more events".
    cyclone = next(
        f for f in page2["features"] if f["properties"]["eventid"] == 1001338
    )
    cyclone["properties"]["eventid"] = 2_000_001
    page2["features"] = [cyclone]
    c = make_collector(min_alert="Orange", alert_overrides={})

    body = points_served_after_collect(client, c, [padded_full_page(), page2])

    assert served_ids(body) == {1001338, 1001335, 2_000_001}


def test_a_current_events_footprint_is_fetched_and_served_without_its_centroid(client):
    c = make_collector(min_alert="Green")
    footprints = {
        ("EQ", 1571932): load_fixture("footprint_EQ_1571932.json"),
        ("WF", 1032639): load_fixture("footprint_WF_1032639.json"),
    }

    run_collect(c, [load_fixture("events_page1.json")], footprints=footprints)

    # The recorded earthquake footprint is a centroid, an alert circle and two shaking
    # contours; the centroid just repeats the event's own marker so it is not stored.
    assert footprint_classes(client, "EQ", 1571932) == [
        "Poly_Circle",
        "Poly_SMPInt_3.5",
        "Poly_SMPInt_4.5",
    ]
    assert footprint_classes(client, "WF", 1032639) == ["Poly_area"]


def test_an_unchanged_event_is_not_re_fetched_but_a_modified_one_is(client):
    c = make_collector(min_alert="Green", types=["WF"])
    footprints = {("WF", 1032639): load_fixture("footprint_WF_1032639.json")}
    page = load_fixture("events_page1.json")
    calls = []

    run_collect(c, [page], footprints=footprints, footprint_calls=calls)
    run_collect(c, [page], footprints=footprints, footprint_calls=calls)
    assert calls == [("WF", 1032639)]  # second cycle: nothing changed, nothing fetched

    wildfire = next(
        f for f in page["features"] if f["properties"]["eventid"] == 1032639
    )
    wildfire["properties"]["datemodified"] = "2026-10-17T09:00:00"
    run_collect(c, [page], footprints=footprints, footprint_calls=calls)
    assert calls == [
        ("WF", 1032639),
        ("WF", 1032639),
    ]  # GDACS modified it -> re-fetched


def test_a_failed_footprint_download_keeps_the_event_and_is_retried_next_cycle(client):
    c = make_collector(min_alert="Green", types=["WF"])
    page = load_fixture("events_page1.json")

    body = points_served_after_collect(
        client, c, [page], footprints={}
    )  # download fails
    assert served_ids(body) == {1032639}
    assert footprint_classes(client, "WF", 1032639) == []

    run_collect(
        c,
        [page],
        footprints={("WF", 1032639): load_fixture("footprint_WF_1032639.json")},
    )
    assert footprint_classes(client, "WF", 1032639) == ["Poly_area"]


def test_footprint_downloads_per_cycle_are_capped_and_the_rest_follow_next_cycle(
    client,
):
    c = make_collector(
        min_alert="Green", types=["EQ", "WF"], max_footprints_per_cycle=1
    )
    page = load_fixture("events_page1.json")
    footprints = {
        ("EQ", 1571932): load_fixture("footprint_EQ_1571932.json"),
        ("WF", 1032639): load_fixture("footprint_WF_1032639.json"),
    }
    calls = []

    run_collect(c, [page], footprints=footprints, footprint_calls=calls)
    assert calls == [("EQ", 1571932)]
    assert footprint_classes(client, "WF", 1032639) == []

    run_collect(c, [page], footprints=footprints, footprint_calls=calls)
    assert calls == [("EQ", 1571932), ("WF", 1032639)]
    assert footprint_classes(client, "WF", 1032639) == ["Poly_area"]


def test_the_list_request_asks_only_for_the_enabled_types_and_passable_alert_levels():
    c = make_collector(
        min_alert="Orange", alert_overrides={"FL": "Green"}, types=["FL", "EQ"]
    )
    sent = []

    run_collect(c, [load_fixture("events_page1.json")], list_params=sent)

    assert set(sent[0]["eventlist"].split(";")) == {"FL", "EQ"}
    # Green is only passable for floods, but the API takes one alert list for all types,
    # so it must include every level at or above the lowest minimum in play.
    assert sent[0]["alertlevel"] == "Green;Orange;Red"
    assert sent[0]["pagenumber"] == 1


def test_the_list_request_drops_green_when_nothing_below_orange_can_pass():
    c = make_collector(min_alert="Orange", alert_overrides={}, types=["TC"])
    sent = []

    run_collect(c, [load_fixture("events_page1.json")], list_params=sent)

    assert sent[0]["alertlevel"] == "Orange;Red"


def test_the_list_request_covers_a_date_window_reaching_back_over_the_lookback():
    c = make_collector(lookback_days=10)
    sent = []

    run_collect(c, [load_fixture("events_page1.json")], list_params=sent)

    today = datetime.now(timezone.utc).date()
    assert sent[0]["todate"] == today.isoformat()
    assert sent[0]["fromdate"] == (today - timedelta(days=10)).isoformat()


def test_a_multi_country_drought_name_is_cleaned_and_shortened(client):
    c = make_collector(min_alert="Orange", types=["DR"])

    body = points_served_after_collect(client, c, [load_fixture("events_drought.json")])

    props = body["features"][0]["properties"]
    # The recorded GDACS name is 311 chars with doubled spaces and a trailing comma;
    # five countries fit in 80 characters and 24 more follow.
    assert props["name"] == (
        "Drought in Austria, Bosnia and Herzegovina, Belgium, Belarus, Switzerland (+24 more)"
    )
    assert len(props["country"]) <= 100
    assert props["country"].endswith("more)")


def test_a_short_name_is_left_exactly_as_it_is(client):
    c = make_collector(min_alert="Orange")

    body = points_served_after_collect(client, c, [load_fixture("events_page1.json")])

    by_id = {f["properties"]["event_id"]: f["properties"] for f in body["features"]}
    assert by_id[1001335]["name"] == "Tropical Cyclone SIMON-26"
