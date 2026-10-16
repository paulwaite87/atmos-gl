#!/usr/bin/env python3
"""Ingest-to-HTTP tests for the Internet Outages layer: InternetOutagesCollector.collect()
runs against IODA events responses with CollectorBase._get patched, fills the in-memory
FakeInternetOutagesAdapter, and every assertion is on what the layer's HTTP route then
serves -- never on adapter internals. The payloads mirror recorded IODA output
(/v2/outages/events?entityType=country): the same keys, with start times built relative
to now so "ongoing" (an event whose end reaches the present) stays true whenever the
suite runs. Prior art: test_tsunami_collector.py, test_gdacs_collector.py."""

import time
from unittest.mock import patch

from terrascope.api import app
from terrascope.collectors.base import CollectorBase
from terrascope.collectors.internet_outages import InternetOutagesCollector
from terrascope.db.internet_outages_adapter import FakeInternetOutagesAdapter
from terrascope.routes.internet_outages import get_internet_outages_adapter

IODA_URL = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
HOUR = 3600


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def ioda_event(
    code, name, signal, *, started_hours_ago, lasted_hours=None, score=5000.0
):
    """One event as IODA serves it. lasted_hours=None means still ongoing: IODA clips
    its duration at the moment of the request."""
    now = int(time.time())
    start = now - int(started_hours_ago * HOUR)
    duration = now - start if lasted_hours is None else int(lasted_hours * HOUR)
    return {
        "location": f"country/{code}",
        "start": start,
        "duration": duration,
        "uncertainty": None,
        "method": "median",
        "datasource": signal,
        "status": 0,
        "fraction": None,
        "score": score,
        "location_name": name,
        "overlaps_window": True,
    }


def ioda_response(*events):
    return FakeResponse({"type": "outages.events", "error": None, "data": list(events)})


def make_collector(**settings):
    c = InternetOutagesCollector.__new__(InternetOutagesCollector)
    c.settings = {"lookback_days": 3, "min_duration_minutes": 30, **settings}
    c.internet_outages_adapter = FakeInternetOutagesAdapter()
    c.datasource_url = lambda key: IODA_URL
    return c


def run_collect(c, response):
    with patch.object(CollectorBase, "_get", side_effect=lambda url, **kw: response):
        c.collect()
    app.dependency_overrides[get_internet_outages_adapter] = (
        lambda: c.internet_outages_adapter
    )


def served(client, **params):
    resp = client.get("/api/internet_outages/countries", params=params)
    assert resp.status_code == 200
    return resp.json()["countries"]


def test_an_ongoing_outage_is_served_for_its_country_with_the_signal_that_saw_it(
    client,
):
    c = make_collector()

    run_collect(
        c, ioda_response(ioda_event("LT", "Lithuania", "gtr", started_hours_ago=160))
    )

    countries = served(client)
    assert set(countries) == {"LT"}
    lithuania = countries["LT"]
    assert lithuania["name"] == "Lithuania"
    assert lithuania["severity"] == 1
    assert lithuania["ongoing"] is True
    [signal] = lithuania["signals"]
    assert signal["signal"] == "gtr"
    assert signal["ongoing"] is True
    assert signal["duration_s"] >= 159 * HOUR


def test_severity_is_how_many_distinct_signals_saw_the_country_down(client):
    c = make_collector()

    run_collect(
        c,
        ioda_response(
            # Armenia: BGP alone.
            ioda_event("AM", "Armenia", "bgp", started_hours_ago=190),
            # Egypt: two events from the same signal still count as one signal.
            ioda_event(
                "EG", "Egypt", "ping-slash24", started_hours_ago=21, lasted_hours=2
            ),
            ioda_event(
                "EG", "Egypt", "ping-slash24", started_hours_ago=10, lasted_hours=1
            ),
            # Namibia: two different signals.
            ioda_event(
                "NA", "Namibia", "ping-slash24", started_hours_ago=19, lasted_hours=2
            ),
            ioda_event("NA", "Namibia", "bgp", started_hours_ago=18, lasted_hours=1),
            # Syria: all three.
            ioda_event("SY", "Syrian Arab Republic", "bgp", started_hours_ago=5),
            ioda_event(
                "SY", "Syrian Arab Republic", "ping-slash24", started_hours_ago=5
            ),
            ioda_event("SY", "Syrian Arab Republic", "gtr", started_hours_ago=4),
            # Peru: a fourth signal (the Merit darknet telescope) is still "all agree".
            ioda_event("PE", "Peru", "bgp", started_hours_ago=6),
            ioda_event("PE", "Peru", "ping-slash24", started_hours_ago=6),
            ioda_event("PE", "Peru", "gtr", started_hours_ago=6),
            ioda_event("PE", "Peru", "merit-nt", started_hours_ago=6),
        ),
    )

    severity = {code: c["severity"] for code, c in served(client, grace_days=5).items()}
    assert severity == {"AM": 1, "EG": 1, "NA": 2, "SY": 3, "PE": 3}


def test_a_country_is_ongoing_if_any_of_its_outages_still_is(client):
    c = make_collector()

    run_collect(
        c,
        ioda_response(
            ioda_event(
                "NA", "Namibia", "ping-slash24", started_hours_ago=19, lasted_hours=2
            ),
            ioda_event("NA", "Namibia", "bgp", started_hours_ago=3),
        ),
    )

    namibia = served(client)["NA"]
    assert namibia["ongoing"] is True
    by_signal = {s["signal"]: s for s in namibia["signals"]}
    assert by_signal["ping-slash24"]["ongoing"] is False
    assert by_signal["ping-slash24"]["duration_s"] == 2 * HOUR
    assert by_signal["bgp"]["ongoing"] is True


def test_outages_shorter_than_the_minimum_duration_are_ignored(client):
    c = make_collector(min_duration_minutes=30)

    run_collect(
        c,
        ioda_response(
            ioda_event(
                "VI",
                "Us Virgin Islands",
                "ping-slash24",
                started_hours_ago=20,
                lasted_hours=0.2,
            ),
            ioda_event("BB", "Barbados", "bgp", started_hours_ago=2, lasted_hours=1),
            # Ongoing but only 10 minutes old: not yet worth showing.
            ioda_event("PG", "Papua New Guinea", "bgp", started_hours_ago=10 / 60),
        ),
    )

    assert set(served(client, grace_days=5)) == {"BB"}


def test_an_ended_outage_stays_for_the_grace_period_then_leaves_the_map(client):
    c = make_collector()

    run_collect(
        c,
        ioda_response(
            ioda_event(
                "EG", "Egypt", "ping-slash24", started_hours_ago=21, lasted_hours=2
            ),
            ioda_event(
                "SY",
                "Syrian Arab Republic",
                "ping-slash24",
                started_hours_ago=70,
                lasted_hours=2,
            ),
            ioda_event("LT", "Lithuania", "gtr", started_hours_ago=160),
        ),
    )

    assert set(served(client)) == {"EG", "LT"}
    assert set(served(client, grace_days=0)) == {"LT"}
    assert set(served(client, grace_days=5)) == {"EG", "SY", "LT"}


def test_refreshing_an_ongoing_outage_updates_it_rather_than_adding_another(client):
    c = make_collector()
    start = int(time.time()) - 5 * HOUR
    first = ioda_event("AM", "Armenia", "bgp", started_hours_ago=5)
    first["start"] = start
    second = dict(first, duration=first["duration"] + 900, score=9999.0)

    run_collect(c, ioda_response(first))
    run_collect(c, ioda_response(second))

    [signal] = served(client)["AM"]["signals"]
    assert signal["ongoing"] is True
    assert signal["duration_s"] >= 5 * HOUR


def test_a_failed_or_garbled_fetch_leaves_what_is_already_stored(client):
    c = make_collector()
    run_collect(
        c, ioda_response(ioda_event("LT", "Lithuania", "gtr", started_hours_ago=160))
    )

    run_collect(c, None)
    assert set(served(client)) == {"LT"}

    run_collect(c, FakeResponse({"error": "maintenance"}))
    assert set(served(client)) == {"LT"}

    run_collect(
        c,
        ioda_response({"location": "country/XX", "datasource": "bgp"}),
    )
    assert set(served(client)) == {"LT"}


def test_an_outage_IODA_stops_reporting_is_closed_not_left_ongoing_forever(client):
    c = make_collector()
    run_collect(
        c,
        ioda_response(
            ioda_event("LT", "Lithuania", "gtr", started_hours_ago=160),
            ioda_event("AM", "Armenia", "bgp", started_hours_ago=190),
        ),
    )

    # Next cycle IODA no longer lists Lithuania (resolved), but still lists Armenia.
    run_collect(
        c, ioda_response(ioda_event("AM", "Armenia", "bgp", started_hours_ago=190))
    )

    countries = served(client)
    assert countries["AM"]["ongoing"] is True
    assert countries["LT"]["ongoing"] is False
    assert served(client, grace_days=0).keys() == {"AM"}.union(set())

    # An empty (but valid) response closes everything that was ongoing.
    run_collect(c, ioda_response())
    assert served(client, grace_days=0) == {}
