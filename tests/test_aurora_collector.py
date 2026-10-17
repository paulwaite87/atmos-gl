#!/usr/bin/env python3
"""AuroraCollector.collect() against a stubbed NOAA SWPC OVATION response, in a temporary
workdir, asserted on the cached grid and its times (lib/aurora.py's loader) -- never on
collector internals. The feed is a full 360 x 181 grid of [lon, lat, probability]
triples, so `ovation()` builds one with the real feed's exact shape and key names
(recorded 2026-10-17) and lets each test place a few known cells in it. Prior art:
test_tsunami_collector.py's stubbed-_get style, test_population_density_collector.py's
temp-workdir style."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from terrascope.collectors.aurora import AuroraCollector
from terrascope.collectors.base import CollectorBase
from terrascope.db.process_status_adapter import FakeProcessStatusAdapter
from terrascope.lib.aurora import aurora_grid_cache_path, load_aurora_grid

URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"


class FakeResponse:
    def __init__(self, doc):
        self.text = json.dumps(doc)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


def ovation(observed="2026-10-17T05:53:00Z", forecast="2026-10-17T06:54:00Z", cells=None):
    """A feed document shaped exactly like the real one: every (lon 0..359, lat -90..90)
    in order, probability 0 except the given {(lon, lat): probability} cells."""
    cells = cells or {}
    return {
        "Observation Time": observed,
        "Forecast Time": forecast,
        "Data Format": "[Longitude, Latitude, Aurora]",
        "coordinates": [
            [lon, lat, cells.get((lon, lat), 0)] for lon in range(360) for lat in range(-90, 91)
        ],
        "type": "MultiPoint",
    }


def make_collector(workdir, datasource=URL):
    c = AuroraCollector.__new__(AuroraCollector)
    c.settings = {}

    def fake_get_setting(section, key, default=None):
        if section == "common" and key == "workdir":
            return workdir
        return default

    c.config = MagicMock()
    c.config.get_setting.side_effect = fake_get_setting
    c.datasource_url = MagicMock(return_value=datasource)
    c.process_status_adapter = FakeProcessStatusAdapter()
    return c


def run_collect(c, response):
    """collect() with the OVATION URL serving `response` (None behaves like a failed
    fetch, which is what CollectorBase._get returns for any HTTP/connection error)."""
    with patch.object(CollectorBase, "_get", return_value=response):
        c.collect()


def stored(workdir):
    probability, observed, forecast = load_aurora_grid(aurora_grid_cache_path(str(workdir)))
    return probability, observed, forecast


def test_a_good_response_is_stored_with_its_grid_and_times(tmp_path):
    c = make_collector(str(tmp_path))

    run_collect(c, FakeResponse(ovation(cells={(10, 65): 42, (350, -70): 7})))

    probability, observed, forecast = stored(tmp_path)
    assert probability.shape == (181, 360)  # latitude -90..90 by longitude 0..359
    assert probability[65 + 90, 10] == 42
    assert probability[-70 + 90, 350] == 7
    assert int(probability.sum()) == 49  # nothing else was set
    assert observed == "2026-10-17T05:53:00Z"
    assert forecast == "2026-10-17T06:54:00Z"


def test_a_newer_response_replaces_the_stored_grid_and_times(tmp_path):
    c = make_collector(str(tmp_path))
    run_collect(c, FakeResponse(ovation(cells={(10, 65): 42})))

    run_collect(
        c,
        FakeResponse(
            ovation(
                observed="2026-10-17T06:03:00Z",
                forecast="2026-10-17T07:04:00Z",
                cells={(200, -60): 88},
            )
        ),
    )

    probability, observed, forecast = stored(tmp_path)
    assert probability[-60 + 90, 200] == 88
    assert probability[65 + 90, 10] == 0  # the earlier oval is gone
    assert (observed, forecast) == ("2026-10-17T06:03:00Z", "2026-10-17T07:04:00Z")


def test_a_failed_fetch_leaves_the_previous_grid_and_times_and_is_reported(tmp_path):
    c = make_collector(str(tmp_path))
    run_collect(c, FakeResponse(ovation(cells={(10, 65): 42})))

    # Raised so the orchestrator records a failure (Data Status shows the error rather
    # than reporting a stale layer as freshly updated), same as SstCollector.
    with pytest.raises(RuntimeError):
        run_collect(c, None)

    probability, observed, _ = stored(tmp_path)
    assert probability[65 + 90, 10] == 42
    assert observed == "2026-10-17T05:53:00Z"


@pytest.mark.parametrize(
    "garbled",
    [
        {"error": "maintenance"},  # no grid at all
        {**ovation(), "coordinates": [[0, 0, 1]]},  # truncated grid
        {**ovation(), "Observation Time": "yesterday-ish"},  # unparseable time
        {**ovation(cells={(10, 65): 250}), "type": "MultiPoint"},  # probability out of range
    ],
    ids=["no-grid", "truncated", "bad-time", "out-of-range"],
)
def test_a_garbled_response_leaves_the_previous_grid_and_times_and_is_reported(tmp_path, garbled):
    c = make_collector(str(tmp_path))
    run_collect(c, FakeResponse(ovation(cells={(10, 65): 42})))

    with pytest.raises(RuntimeError):
        run_collect(c, FakeResponse(garbled))

    probability, observed, _ = stored(tmp_path)
    assert probability[65 + 90, 10] == 42
    assert observed == "2026-10-17T05:53:00Z"
