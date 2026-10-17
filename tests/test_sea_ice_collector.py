#!/usr/bin/env python3
"""SeaIceCollector.collect() against stubbed downloads, in a temporary workdir, asserted
on the cache file the sea ice updater reads -- never on collector internals. NOAA's
OISST ice file is one yearly netCDF (icec.day.mean.<year>.nc) beside the SST ones;
`download_whole` (the network call) and `remote_is_newer` (the HEAD freshness check) are
the only seams stubbed. Prior art: test_sst_mode_switch.py's collector tests,
test_aurora_collector.py."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from terrascope.collectors import sea_ice as mod
from terrascope.collectors.sea_ice import SeaIceCollector
from terrascope.db.process_status_adapter import FakeProcessStatusAdapter
from terrascope.lib.sea_ice import sea_ice_cache_path

BASE = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"


def make_collector(workdir, datasource=BASE):
    c = SeaIceCollector.__new__(SeaIceCollector)
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


def pin_year(monkeypatch, year, month=10):
    class PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(year, month, 17, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(mod, "datetime", PinnedDatetime)


def not_found():
    return requests.HTTPError("404 Not Found", response=SimpleNamespace(status_code=404))


def fake_downloads(files):
    """A download_whole stand-in serving {url: bytes}; a value that is an exception is
    raised instead. Every URL asked for is appended to `.requested`."""
    requested = []

    def download(url, timeout=120, headers=None):
        requested.append(url)
        result = files[url]
        if isinstance(result, Exception):
            raise result
        return result

    download.requested = requested
    return download


def run_collect(c, downloads, remote_newer=True):
    with patch.object(mod, "download_whole", downloads), patch.object(
        mod, "remote_is_newer", return_value=remote_newer
    ):
        c.collect()


def cached(workdir):
    with open(sea_ice_cache_path(str(workdir)), "rb") as f:
        return f.read()


def test_the_current_years_ice_file_is_downloaded_into_the_cache(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2026)
    c = make_collector(str(tmp_path))
    downloads = fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-2026"})

    run_collect(c, downloads)

    assert cached(tmp_path) == b"ICE-2026"
    assert downloads.requested == [f"{BASE}/icec.day.mean.2026.nc"]


def test_nothing_is_downloaded_when_the_remote_file_is_not_newer(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2026)
    c = make_collector(str(tmp_path))
    run_collect(c, fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-DAY-1"}))

    downloads = fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-DAY-2"})
    run_collect(c, downloads, remote_newer=False)

    assert downloads.requested == []
    assert cached(tmp_path) == b"ICE-DAY-1"


def test_a_newer_remote_file_replaces_the_cache(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2026)
    c = make_collector(str(tmp_path))
    run_collect(c, fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-DAY-1"}))

    run_collect(c, fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-DAY-2"}))

    assert cached(tmp_path) == b"ICE-DAY-2"


def test_a_failed_download_leaves_the_previous_file_and_is_reported(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2026)
    c = make_collector(str(tmp_path))
    url = f"{BASE}/icec.day.mean.2026.nc"
    run_collect(c, fake_downloads({url: b"ICE-DAY-1"}))

    # Raised so the orchestrator records a failure (Data Status shows the error rather
    # than a stale layer reading as freshly updated), same as SstCollector.
    with pytest.raises(RuntimeError, match="icec.day.mean.2026.nc"):
        run_collect(c, fake_downloads({url: requests.ConnectionError("reset")}))

    assert cached(tmp_path) == b"ICE-DAY-1"
    assert os.listdir(os.path.dirname(sea_ice_cache_path(str(tmp_path)))) == [
        os.path.basename(sea_ice_cache_path(str(tmp_path)))
    ]  # no half-written .tmp left behind


def test_early_in_the_year_a_missing_new_file_falls_back_to_last_years(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2027, month=1)
    c = make_collector(str(tmp_path))
    downloads = fake_downloads(
        {
            f"{BASE}/icec.day.mean.2027.nc": not_found(),  # NOAA has not published it yet
            f"{BASE}/icec.day.mean.2026.nc": b"ICE-2026-FULL-YEAR",
        }
    )

    run_collect(c, downloads)

    assert cached(tmp_path) == b"ICE-2026-FULL-YEAR"
    assert downloads.requested == [
        f"{BASE}/icec.day.mean.2027.nc",
        f"{BASE}/icec.day.mean.2026.nc",
    ]


def test_a_transient_failure_never_falls_back_to_last_year(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2026)
    c = make_collector(str(tmp_path))
    run_collect(c, fake_downloads({f"{BASE}/icec.day.mean.2026.nc": b"ICE-DAY-1"}))
    downloads = fake_downloads(
        {
            f"{BASE}/icec.day.mean.2026.nc": requests.ConnectionError("reset"),
            f"{BASE}/icec.day.mean.2025.nc": b"ICE-LAST-YEAR",
        }
    )

    with pytest.raises(RuntimeError):
        run_collect(c, downloads)

    assert downloads.requested == [f"{BASE}/icec.day.mean.2026.nc"]
    assert cached(tmp_path) == b"ICE-DAY-1"  # an October outage must not serve January's ice


def test_neither_year_being_available_is_reported(tmp_path, monkeypatch):
    pin_year(monkeypatch, 2027, month=1)
    c = make_collector(str(tmp_path))
    downloads = fake_downloads(
        {
            f"{BASE}/icec.day.mean.2027.nc": not_found(),
            f"{BASE}/icec.day.mean.2026.nc": not_found(),
        }
    )

    with pytest.raises(RuntimeError):
        run_collect(c, downloads)

    assert not os.path.exists(sea_ice_cache_path(str(tmp_path)))
