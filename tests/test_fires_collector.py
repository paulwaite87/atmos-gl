#!/usr/bin/env python3
"""Tests for FiresCollector.collect() -- specifically the dual-source (NOAA-20 + NOAA-21)
amalgamation added to catch detections either satellite's overpass alone would miss.
Mirrors SstCollector.collect()'s per-mode-independent-but-raises-after-all-attempts
pattern: every source in _SOURCES is fetched regardless of an earlier failure, but any
failure still surfaces as a raised error so the Data Status UI doesn't report success
while one satellite's detections silently went missing.
"""
from unittest.mock import MagicMock, patch

import requests

from atmos_gl.collectors.fires import FiresCollector, _SOURCES


def make_collector(settings=None):
    c = FiresCollector.__new__(FiresCollector)
    c.settings = settings if settings is not None else {"api_key": "test-key"}
    c.fire_adapter = MagicMock()
    c.datasource_url = MagicMock(return_value="https://firms.example/api/area/csv")
    return c


def _csv_response(rows):
    """rows: list of (lat, lon, satellite) tuples -> a minimal valid FIRMS CSV body."""
    header = "latitude,longitude,bright_ti4,frp,confidence,satellite,daynight,acq_date,acq_time"
    lines = [header]
    for lat, lon, satellite in rows:
        lines.append(f"{lat},{lon},330.5,12.3,n,{satellite},D,2026-07-17,1230")
    return "\n".join(lines)


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_collect_fetches_both_sources_and_upserts_combined_rows():
    c = make_collector()
    noaa20_body = _csv_response([(41.0, -3.2, "N20")])
    noaa21_body = _csv_response([(41.5, -3.5, "N21")])

    def fake_get(url, **kwargs):
        if "VIIRS_NOAA20_NRT" in url:
            return _FakeResponse(noaa20_body)
        if "VIIRS_NOAA21_NRT" in url:
            return _FakeResponse(noaa21_body)
        raise AssertionError(f"unexpected source in url: {url}")

    with patch("atmos_gl.collectors.fires.requests.get", side_effect=fake_get) as mock_get:
        c.collect()

    assert mock_get.call_count == len(_SOURCES) == 2
    (upserted_rows,), _ = c.fire_adapter.upsert_fires.call_args
    assert len(upserted_rows) == 2
    satellites = {r["satellite"] for r in upserted_rows}
    assert satellites == {"N20", "N21"}
    # Distinct ids -- amalgamation needs no dedup because satellite rides in the id.
    ids = {r["id"] for r in upserted_rows}
    assert len(ids) == 2
    c.fire_adapter.delete_expired.assert_called_once()


def test_collect_one_source_failing_still_upserts_the_other_but_raises():
    c = make_collector()
    noaa20_body = _csv_response([(41.0, -3.2, "N20")])

    def fake_get(url, **kwargs):
        if "VIIRS_NOAA20_NRT" in url:
            return _FakeResponse(noaa20_body)
        raise requests.ConnectionError("NOAA-21 endpoint unreachable")

    with patch("atmos_gl.collectors.fires.requests.get", side_effect=fake_get):
        try:
            c.collect()
            assert False, "expected collect() to raise when one source fails"
        except RuntimeError as e:
            assert "VIIRS_NOAA21_NRT" in str(e)

    # The successful source's rows were still upserted -- one satellite's outage
    # doesn't block the other's detections from landing.
    (upserted_rows,), _ = c.fire_adapter.upsert_fires.call_args
    assert len(upserted_rows) == 1
    assert upserted_rows[0]["satellite"] == "N20"


def test_collect_both_sources_failing_raises_and_upserts_nothing():
    c = make_collector()

    with patch(
        "atmos_gl.collectors.fires.requests.get",
        side_effect=requests.ConnectionError("FIRMS down"),
    ):
        try:
            c.collect()
            assert False, "expected collect() to raise when both sources fail"
        except RuntimeError as e:
            assert "2/2" in str(e)

    (upserted_rows,), _ = c.fire_adapter.upsert_fires.call_args
    assert upserted_rows == []


def test_collect_skips_entirely_without_api_key():
    c = make_collector(settings={})

    with patch("atmos_gl.collectors.fires.requests.get") as mock_get:
        c.collect()

    mock_get.assert_not_called()
    c.fire_adapter.upsert_fires.assert_not_called()


def test_collect_bad_csv_body_from_one_source_degrades_without_raising():
    """An unexpected (non-fire-CSV) response body from one source is logged and
    treated as zero rows from that source, not a hard failure -- matches the original
    single-source collector's behaviour for this case."""
    c = make_collector()
    noaa21_body = _csv_response([(41.5, -3.5, "N21")])

    def fake_get(url, **kwargs):
        if "VIIRS_NOAA20_NRT" in url:
            return _FakeResponse("<html>rate limited</html>")
        return _FakeResponse(noaa21_body)

    with patch("atmos_gl.collectors.fires.requests.get", side_effect=fake_get):
        c.collect()  # must not raise

    (upserted_rows,), _ = c.fire_adapter.upsert_fires.call_args
    assert len(upserted_rows) == 1
    assert upserted_rows[0]["satellite"] == "N21"
