#!/usr/bin/env python3
"""CamsEgg4BaselineCollector: fetches the CAMS EGG4 reanalysis baseline field for the
configured greenhouse_gases.baseline_year via the CDS API (submit-then-poll), bounded
by a bounded-blocking timeout so a slow/queued job never stalls collect_once()'s
single-threaded synchronous loop indefinitely (see the GHG design grill). Mocks the
cdsapi.Client boundary, matching the SST collector test seam.
"""
import concurrent.futures
import os
from unittest.mock import MagicMock, patch

import pytest

from atmos_gl.collectors.greenhouse_gases import (
    CamsEgg4BaselineCollector,
    build_egg4_request,
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


def _fake_retrieve_writing_zip(make_netcdf_zip_bytes, content: bytes):
    zip_bytes = make_netcdf_zip_bytes("data.nc", content)

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    return fake_retrieve


def test_build_egg4_request_spans_the_full_baseline_year_at_3_hourly_steps():
    request = build_egg4_request(2010)
    assert request["date"] == "2010-01-01/2010-12-31"
    assert request["step"] == ["0", "3", "6", "9", "12", "15", "18", "21"]
    assert set(request["variable"]) == {
        "co2_column_mean_molar_fraction", "ch4_column_mean_molar_fraction",
    }
    assert request["data_format"] == "netcdf_zip"


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


def test_collect_fetches_unzips_and_caches_when_missing(tmp_path, monkeypatch, make_netcdf_zip_bytes):
    year = 2015
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": year}, workdir=str(tmp_path))

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = _fake_retrieve_writing_zip(make_netcdf_zip_bytes, b"fake-egg4-netcdf")

    with patch(
        "atmos_gl.collectors.greenhouse_gases.cdsapi.Client", return_value=mock_client
    ) as mock_client_cls:
        c.collect()

    mock_client_cls.assert_called_once_with(url="https://ads.example.com/api", key="secret-token")
    dest = egg4_baseline_cache_path(str(tmp_path), year)
    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"fake-egg4-netcdf"


def test_collect_clamps_baseline_year_outside_egg4_coverage(tmp_path, monkeypatch, make_netcdf_zip_bytes):
    c = make_bare_egg4_collector(monkeypatch, settings={"baseline_year": 2099}, workdir=str(tmp_path))
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = _fake_retrieve_writing_zip(make_netcdf_zip_bytes, b"fake-egg4-netcdf")

    with patch(
        "atmos_gl.collectors.greenhouse_gases.cdsapi.Client", return_value=mock_client
    ):
        c.collect()

    assert mock_client.retrieve.call_count == 1
    request = mock_client.retrieve.call_args[0][1]
    assert request["date"] == "2020-01-01/2020-12-31"  # clamped to BASELINE_YEAR_MAX


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


def test_retrieve_with_timeout_actually_releases_the_calling_thread_at_timeout_s():
    """Regression guard for a real bug found via live testing: a genuinely slow call
    (here, much slower than timeout_s) must make the calling thread stop waiting at
    timeout_s -- not "eventually" once the slow call itself finishes. The earlier
    version used `with ThreadPoolExecutor(...) as pool:`, whose __exit__ always calls
    shutdown(wait=True), re-blocking the caller until the still-running background
    thread finishes regardless of the TimeoutError already raised -- silently
    defeating the entire point of a bounded timeout. A real CDS request that should
    have timed out at 300s was observed live still blocking the calling thread 12+
    minutes later because of exactly this. The previous version of this test used a
    slow call only slightly slower than its timeout (0.3s vs 0.02s), so the bug's
    ~0.28s of extra blocking was too small to notice in a wall-clock assertion --
    this test uses a much larger gap specifically so the regression would be
    unmissable."""
    import time

    def very_slow_retrieve(dataset, request, target):
        time.sleep(5)

    client = MagicMock()
    client.retrieve.side_effect = very_slow_retrieve

    start = time.monotonic()
    with pytest.raises(concurrent.futures.TimeoutError):
        _retrieve_with_timeout(client, "dataset", {}, "target", timeout_s=0.1)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"took {elapsed:.2f}s to raise for a 0.1s timeout -- the calling thread is "
        f"waiting for the slow background call instead of actually being bounded"
    )


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
