#!/usr/bin/env python3
"""Ingest-to-HTTP tests for the GDACS Alerts layer: GdacsCollector.collect() runs against
recorded GDACS list/footprint responses (tests/fixtures/gdacs/, trimmed real feed
output) with CollectorBase._get patched, fills the in-memory FakeGdacsAdapter, and every
assertion is on what the layer's HTTP routes then serve -- never on adapter internals.
Prior art: test_quakes_collector.py (collector) + test_quakes_route.py (route seam)."""

import json
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


def points_served_after_collect(client, c, pages):
    """Run collect() with the list endpoint serving `pages` (page 1, 2, ...), then GET
    the points route backed by the collector's own fake adapter."""

    def fake_get(url, **kwargs):
        page = int(kwargs.get("params", {}).get("pagenumber", 1))
        return FakeResponse(pages[page - 1]) if page <= len(pages) else None

    with patch.object(CollectorBase, "_get", side_effect=fake_get):
        c.collect()
    app.dependency_overrides[get_gdacs_adapter] = lambda: c.gdacs_adapter
    resp = client.get("/api/gdacs/geojson")
    assert resp.status_code == 200
    return resp.json()


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
