#!/usr/bin/env python3
"""AirQualityCollector: fetches CAMS's current PM2.5/PM10/smoke-AOD forecast snapshot
via the CDS API. Same test seam as CamsGhgForecastCollector (mirrors
tests/test_greenhouse_gases_forecast_collector.py) -- mock the cdsapi.Client boundary,
assert on cache file existence and the constructed request, not on netCDF contents.
"""
import concurrent.futures
import os
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.air_quality import (
    AirQualityCollector,
    build_air_quality_request,
)
from atmos_gl.lib.air_quality import camsforecast_cache_path


def make_bare_air_quality_collector(settings=None, workdir=".", api_key="secret-token", monkeypatch=None):
    c = AirQualityCollector.__new__(AirQualityCollector)
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


def test_build_air_quality_request_targets_leadtime_zero_and_all_five_variables():
    request = build_air_quality_request("2026-07-27", "12:00")
    assert request["leadtime_hour"] == ["0"]
    assert request["date"] == "2026-07-27/2026-07-27"
    assert request["time"] == ["12:00"]
    assert request["type"] == ["forecast"]
    assert set(request["variable"]) == {
        "particulate_matter_2.5um", "particulate_matter_10um",
        "total_aerosol_optical_depth_550nm", "total_column_sulphur_dioxide",
        "total_column_volcanic_sulphur_dioxide",
    }
    assert request["data_format"] == "netcdf_zip"


def test_collect_fetches_unzips_and_caches(tmp_path, monkeypatch, make_netcdf_zip_bytes):
    c = make_bare_air_quality_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    zip_bytes = make_netcdf_zip_bytes("data.nc", b"fake-netcdf-bytes")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.air_quality.cdsapi.Client", return_value=mock_client
    ) as mock_client_cls:
        c.collect()

    mock_client_cls.assert_called_once_with(url="https://ads.example.com/api", key="secret-token")
    dest = camsforecast_cache_path(str(tmp_path))
    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"fake-netcdf-bytes"


def test_collect_returns_gracefully_without_raising_on_timeout(tmp_path, monkeypatch):
    c = make_bare_air_quality_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)

    with patch("atmos_gl.collectors.air_quality.cdsapi.Client"), patch(
        "atmos_gl.lib.cds_client.retrieve_with_timeout",
        side_effect=concurrent.futures.TimeoutError,
    ):
        c.collect()  # must not raise

    assert not os.path.exists(camsforecast_cache_path(str(tmp_path)))


def test_collect_skips_without_raising_when_no_api_key(tmp_path, monkeypatch):
    c = make_bare_air_quality_collector(
        workdir=str(tmp_path), api_key=None, monkeypatch=monkeypatch
    )

    with patch("atmos_gl.collectors.air_quality.cdsapi.Client") as mock_client_cls:
        c.collect()

    mock_client_cls.assert_not_called()


def test_collect_refetches_every_call(tmp_path, monkeypatch, make_netcdf_zip_bytes):
    """Current-conditions collector -- collect() must always attempt a fresh fetch
    when called; _drive()'s runs_per_day cadence is what limits how often that
    happens, not an existence check inside collect() itself."""
    c = make_bare_air_quality_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    zip_bytes = make_netcdf_zip_bytes("data.nc", b"second-fetch")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    dest = camsforecast_cache_path(str(tmp_path))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"stale-data")

    with patch(
        "atmos_gl.collectors.air_quality.cdsapi.Client", return_value=mock_client
    ):
        c.collect()

    mock_client.retrieve.assert_called_once()
    assert open(dest, "rb").read() == b"second-fetch"


def test_collect_falls_back_to_the_next_run_when_the_first_is_not_yet_published(
    tmp_path, monkeypatch, make_netcdf_zip_bytes
):
    """CAMS issues two runs/day (00Z/12Z, unlike greenhouse_gases' one/day) -- the
    candidate search must fall back across BOTH the time-of-day and day axes, newest
    first, not just across days."""
    c = make_bare_air_quality_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    zip_bytes = make_netcdf_zip_bytes("data.nc", b"second-candidate-run")
    seen = []

    def fake_retrieve(dataset, request, target):
        seen.append((request["date"].split("/")[0], request["time"][0]))
        if len(seen) == 1:
            raise RuntimeError("400 Client Error: run not published yet")
        with open(target, "wb") as f:
            f.write(zip_bytes)

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.air_quality.cdsapi.Client", return_value=mock_client
    ):
        c.collect()

    assert len(seen) == 2
    assert seen[0][1] == "12:00" and seen[1][1] == "00:00"  # same day, earlier run
    assert seen[0][0] == seen[1][0]
    dest = camsforecast_cache_path(str(tmp_path))
    assert open(dest, "rb").read() == b"second-candidate-run"


def test_collect_gives_up_gracefully_when_no_recent_run_is_available(tmp_path, monkeypatch):
    c = make_bare_air_quality_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = RuntimeError("400 Client Error")

    with patch(
        "atmos_gl.collectors.air_quality.cdsapi.Client", return_value=mock_client
    ):
        c.collect()  # must not raise

    # _CAMS_FORECAST_SEARCH_DAYS (3) x len(_CAMS_RUN_TIMES) (2)
    assert mock_client.retrieve.call_count == 6
    assert not os.path.exists(camsforecast_cache_path(str(tmp_path)))
