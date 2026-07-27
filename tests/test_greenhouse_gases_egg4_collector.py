#!/usr/bin/env python3
"""CamsEgg4BaselineCollector: fetches the CAMS EGG4 reanalysis baseline field for the
configured greenhouse_gases.baseline_year via the CDS API (submit-then-poll), bounded
by a bounded bounded-blocking timeout so a slow/queued job never stalls
collect_once()'s single-threaded synchronous loop indefinitely (see the GHG design
grill). Mocks the cdsapi.Client boundary, matching the SST collector test seam.
"""
import concurrent.futures
import os
from unittest.mock import MagicMock, patch

import pytest

from atmos_gl.collectors.greenhouse_gases import (
    CamsEgg4BaselineCollector,
    _retrieve_with_timeout,
)
from atmos_gl.lib.greenhouse_gases import egg4_baseline_cache_path


def make_bare_egg4_collector(
    monkeypatch, settings=None, workdir=".", api_key="secret-token"
):
    c = CamsEgg4BaselineCollector.__new__(CamsEgg4BaselineCollector)
    c.settings = settings or {}
    url = c.settings.get("cams_ads_url", "https://ads.example.com/api")

    def fake_get_setting(section, key, default=None):
        if section == "data_collector" and key == "datasources":
            return {"cams_ads": url}
        if section == "common" and key == "workdir":
            return workdir
        return default

    c.config = MagicMock()
    c.config.get_setting.side_effect = fake_get_setting
    if api_key is not None:
        monkeypatch.setenv("CDSAPI_KEY", api_key)
    else:
        monkeypatch.delenv("CDSAPI_KEY", raising=False)
    return c


def test_collect_skips_when_configured_year_already_cached(tmp_path, monkeypatch):
    year = 2010
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": year}, workdir=str(tmp_path))
    dest = egg4_baseline_cache_path(str(tmp_path), year)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"already-cached")

    with patch("atmos_gl.collectors.greenhouse_gases.cdsapi.Client") as mock_client_cls:
        c.collect()

    mock_client_cls.assert_not_called()
    assert dest and open(egg4_baseline_cache_path(str(tmp_path), year), "rb").read() == b"already-cached"


def test_collect_fetches_and_caches_when_missing(tmp_path, monkeypatch):
    year = 2015
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": year}, workdir=str(tmp_path))

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(b"fake-egg4-netcdf")

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.greenhouse_gases.cdsapi.Client", return_value=mock_client
    ) as mock_client_cls:
        c.collect()

    mock_client_cls.assert_called_once_with(url="https://ads.example.com/api", key="secret-token")
    dest = egg4_baseline_cache_path(str(tmp_path), year)
    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"fake-egg4-netcdf"


def test_collect_clamps_baseline_year_outside_egg4_coverage(tmp_path, monkeypatch):
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": 2099}, workdir=str(tmp_path))

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(b"fake-egg4-netcdf")

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.greenhouse_gases.cdsapi.Client", return_value=mock_client
    ):
        c.collect()

    assert mock_client.retrieve.call_count == 1
    request = mock_client.retrieve.call_args[0][1]
    assert request["year"] == "2020"  # clamped to BASELINE_YEAR_MAX


def test_collect_returns_gracefully_without_raising_on_timeout(tmp_path, monkeypatch):
    year = 2005
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": year}, workdir=str(tmp_path))

    with patch("atmos_gl.collectors.greenhouse_gases.cdsapi.Client"), patch(
        "atmos_gl.collectors.greenhouse_gases._retrieve_with_timeout",
        side_effect=concurrent.futures.TimeoutError,
    ):
        c.collect()  # must not raise

    assert not os.path.exists(egg4_baseline_cache_path(str(tmp_path), year))


def test_collect_skips_without_raising_when_no_api_key(tmp_path, monkeypatch):
    c = make_bare_egg4_collector(
        monkeypatch, settings={"baseline_year": 2003}, workdir=str(tmp_path), api_key=None
    )

    with patch("atmos_gl.collectors.greenhouse_gases.cdsapi.Client") as mock_client_cls:
        c.collect()

    mock_client_cls.assert_not_called()


def test_retrieve_with_timeout_raises_timeout_error_for_a_slow_call():
    def slow_retrieve(dataset, request, target):
        import time

        time.sleep(0.3)

    client = MagicMock()
    client.retrieve.side_effect = slow_retrieve

    with pytest.raises(concurrent.futures.TimeoutError):
        _retrieve_with_timeout(client, "dataset", {}, "target", timeout_s=0.02)


def test_data_status_reports_coverage_not_time_decay(tmp_path, monkeypatch):
    year = 2003
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": year}, workdir=str(tmp_path))
    c.process_status_adapter = MagicMock()
    c.process_status_adapter.get_process_status.return_value = None

    status_before = c.data_status()
    assert status_before["percent"] == 0.0

    dest = egg4_baseline_cache_path(str(tmp_path), year)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"cached")

    status_after = c.data_status()
    assert status_after["percent"] == 100.0
