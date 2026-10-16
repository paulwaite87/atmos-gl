#!/usr/bin/env python3
"""Ingest-to-HTTP tests for the Tsunami Warnings layer: TsunamiCollector.collect() runs
against recorded NOAA CAP documents (tests/fixtures/tsunami/, real feed output with the
XML signature stripped) with CollectorBase._get patched, fills the in-memory
FakeTsunamiAdapter, and every assertion is on what the layer's HTTP routes then serve --
never on adapter internals. Prior art: test_gdacs_collector.py."""

import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from terrascope.api import app
from terrascope.collectors.base import CollectorBase
from terrascope.collectors.tsunami import TsunamiCollector
from terrascope.db.tsunami_adapter import FakeTsunamiAdapter
from terrascope.routes.tsunami import get_tsunami_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "tsunami"
PTWC_URL = "https://www.tsunami.gov/events/xml/PHEBCAP.xml"
NTWC_URL = "https://www.tsunami.gov/events/xml/PAAQCAP.xml"


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def json(self):
        return json.loads(self.text)


@pytest.fixture(autouse=True)
def forget_unresolved_zones():
    """Retired-zone memory is process-level; keep it from leaking between tests."""
    TsunamiCollector._unresolved.clear()


def fixture(name):
    return FakeResponse((FIXTURES / name).read_text())


NWS_ZONE = "https://api.weather.gov/zones/{type}/{code}"
# Two zones of the 2012 bulletin that the NWS API can still serve (small stand-in
# polygons: the real ones run to ~350 KB each): CAZ042 is in the Warning product and
# resolves as a public zone; PZZ530 is in the Advisory product and only as a coastal
# marine zone. The bulletin's other zones are retired and 404.
ZONES_2012 = {
    NWS_ZONE.format(type="public", code="CAZ042"): fixture("zone_public_CAZ042.json"),
    NWS_ZONE.format(type="coastal", code="PZZ530"): fixture("zone_coastal_PZZ530.json"),
}


def make_collector(**settings):
    c = TsunamiCollector.__new__(TsunamiCollector)
    c.settings = {
        "feeds": [PTWC_URL, NTWC_URL],
        "include_information": False,
        **settings,
    }
    c.tsunami_adapter = FakeTsunamiAdapter()
    return c


def run_collect(c, feeds, requested=None):
    """Run collect() with each URL serving the given response; any URL not listed behaves
    like a failed fetch (None). Every URL requested is appended to `requested` if given.
    """

    def fake_get(url, **kwargs):
        if requested is not None:
            requested.append(url)
        return feeds.get(url)

    with patch.object(CollectorBase, "_get", side_effect=fake_get):
        c.collect()
    app.dependency_overrides[get_tsunami_adapter] = lambda: c.tsunami_adapter


def served(client, **params):
    resp = client.get("/api/tsunami/geojson", params=params)
    assert resp.status_code == 200
    return resp.json()["features"]


def test_an_information_statement_is_served_when_information_is_enabled(client):
    c = make_collector(include_information=True)

    run_collect(c, {PTWC_URL: fixture("cap_information_pheb.xml"), NTWC_URL: None})

    [kamchatka] = served(client)
    assert kamchatka["geometry"] == {"type": "Point", "coordinates": [157.428, 51.86]}
    p = kamchatka["properties"]
    assert p["centre"] == "PHEB"
    assert p["event_id"] == "26289000"
    assert p["category"] == "Information"
    assert p["bulletin_number"] == 1
    assert p["location_name"] == "NEAR THE EAST COAST OF KAMCHATKA RUSSIA"
    assert p["magnitude"] == 7.0
    assert p["magnitude_type"] == "Mwp"
    assert p["depth_km"] == 148
    assert p["origin_time"] == "2026-10-16T03:35:31Z"
    assert p["expires"] == "2026-10-16T04:46:40Z"
    assert p["bulletin_url"] == (
        "http://www.tsunami.gov/events/PHEB/2026/10/16/26289000/1/WEGM42/WEGM42.txt"
    )


def test_information_statements_are_left_out_by_default(client):
    c = make_collector()

    run_collect(c, {PTWC_URL: fixture("cap_information_pheb.xml"), NTWC_URL: None})

    assert served(client) == []


def test_a_warning_bulletin_is_served_at_its_highest_category_even_when_it_also_carries_advisories(
    client,
):
    c = make_collector()

    # The 2012 NTWC bulletin holds three products (Warning, Advisory and Watch) for one
    # earthquake. It is long expired, so ask for a grace period that reaches back to it.
    run_collect(c, {PTWC_URL: None, NTWC_URL: fixture("cap_warning_paaq_2012.xml")})

    [event] = served(client, grace_days=100000)
    p = event["properties"]
    assert (p["centre"], p["event_id"]) == ("PAAQ", "maiqdx")
    assert p["category"] == "Warning"
    assert p["bulletin_number"] == 2
    assert p["magnitude"] == 8.4
    assert p["magnitude_type"] == "Ml"
    assert p["depth_km"] == 6
    assert event["geometry"]["coordinates"] == [-115.633, 32.776]


def test_a_later_bulletin_for_the_same_earthquake_replaces_the_earlier_one(client):
    c = make_collector(include_information=True)
    first = fixture("cap_information_pheb.xml")
    escalated = FakeResponse(
        first.text.replace("PHEB-1-26289000", "PHEB-2-26289000").replace(
            "Tsunami Information", "Tsunami Advisory"
        )
    )

    run_collect(c, {PTWC_URL: first, NTWC_URL: None})
    run_collect(c, {PTWC_URL: escalated, NTWC_URL: None})

    [event] = served(client)
    assert event["properties"]["category"] == "Advisory"
    assert event["properties"]["bulletin_number"] == 2


def test_a_garbled_feed_does_not_stop_the_other_centre_being_processed(client):
    c = make_collector(include_information=True)

    run_collect(
        c,
        {
            PTWC_URL: FakeResponse("<html>maintenance</html>"),
            NTWC_URL: fixture("cap_information_pheb.xml"),
        },
    )

    [event] = served(client)
    assert event["properties"]["event_id"] == "26289000"


def test_clicking_a_bulletin_serves_its_zones_coloured_by_the_product_they_belong_to(
    client,
):
    c = make_collector()

    run_collect(c, {NTWC_URL: fixture("cap_warning_paaq_2012.xml"), **ZONES_2012})

    resp = client.get("/api/tsunami/PAAQ/maiqdx/footprint")
    assert resp.status_code == 200
    zones = {f["properties"]["zone_code"]: f for f in resp.json()["features"]}
    # Retired zones (404 at the NWS) are skipped; the rest are still shown.
    assert set(zones) == {"CAZ042", "PZZ530"}
    assert zones["CAZ042"]["properties"]["category"] == "Warning"
    assert zones["PZZ530"]["properties"]["category"] == "Advisory"
    assert zones["CAZ042"]["geometry"]["type"] == "Polygon"


def test_a_bulletin_with_no_resolvable_zones_has_an_empty_footprint(client):
    c = make_collector()

    run_collect(c, {NTWC_URL: fixture("cap_warning_paaq_2012.xml")})

    resp = client.get("/api/tsunami/PAAQ/maiqdx/footprint")
    assert resp.json() == {"type": "FeatureCollection", "features": []}


def test_each_zone_is_downloaded_only_once_however_many_cycles_see_it(client):
    c = make_collector()
    feeds = {NTWC_URL: fixture("cap_warning_paaq_2012.xml"), **ZONES_2012}
    requested = []

    run_collect(c, feeds, requested)
    run_collect(c, feeds, requested)

    zone_requests = [u for u in requested if "/zones/" in u]
    assert zone_requests.count(NWS_ZONE.format(type="public", code="CAZ042")) == 1
    assert zone_requests.count(NWS_ZONE.format(type="coastal", code="PZZ530")) == 1


def test_range_notation_in_a_bulletins_zone_list_is_expanded(client):
    c = make_collector(include_information=True)
    requested = []

    # This bulletin lists its zones as "GUZ001-MPZ001>003-160546-".
    run_collect(c, {PTWC_URL: fixture("cap_information_pheb.xml")}, requested)

    asked_for = {u.split("/")[-1] for u in requested if "/zones/" in u}
    assert asked_for == {"GUZ001", "MPZ001", "MPZ002", "MPZ003"}


def test_a_cold_start_spreads_zone_downloads_over_several_cycles(client):
    c = make_collector(max_zones_per_cycle=1)
    feeds = {NTWC_URL: fixture("cap_warning_paaq_2012.xml"), **ZONES_2012}

    run_collect(c, feeds)
    run_collect(c, feeds)

    resp = client.get("/api/tsunami/PAAQ/maiqdx/footprint")
    assert {f["properties"]["zone_code"] for f in resp.json()["features"]} == {
        "CAZ042",
        "PZZ530",
    }


def test_the_centres_instruction_text_is_served_as_the_note_unless_it_is_just_n_a(
    client,
):
    c = make_collector(include_information=True)

    run_collect(
        c,
        {
            PTWC_URL: fixture("cap_information_pheb.xml"),
            NTWC_URL: fixture("cap_information_paaq.xml"),
        },
    )

    notes = {f["properties"]["centre"]: f["properties"]["note"] for f in served(client)}
    assert notes["PAAQ"] == (
        "There is no tsunami danger for the U.S. West Coast, British Columbia, or "
        "Alaska. Based on the depth of the earthquake, a tsunami is not expected. This "
        "will be the only U.S. National Tsunami Warning Center message for this event "
        "unless additional information becomes available. Refer to the internet site "
        "tsunami.gov for more information."
    )
    assert notes["PHEB"] is None
