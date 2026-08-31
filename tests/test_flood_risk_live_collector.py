#!/usr/bin/env python3
"""FloodRiskLiveCollector: GloFAS ensemble discharge forecast classified per grid
cell against ETH's Gumbel-fit return-period thresholds (see issue #371).

Mirrors test_greenhouse_gases_forecast_collector.py's seam (mock the cdsapi.Client
boundary, assert on cache/store calls, not on real network access) for the
credential/search-fallback tests, and test_field_collector_base_data_status.py's
bare-collector construction for data_status(). _process_and_store is exercised
against tiny REAL netCDF fixtures (written via xarray) since its job is genuinely
parsing/regridding/classifying array data, not just orchestrating a fetch.
"""
import concurrent.futures
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from atmos_gl.collectors.flood_risk import FloodRiskLiveCollector
from atmos_gl.lib.flood_risk import (
    GLOFAS_LEADTIME_HOURS,
    GLOFAS_SEARCH_DAYS,
    gumbel_threshold_discharge,
    glofas_forecast_cache_path,
)


def make_bare_live_collector(
    workdir=".", api_key="glofas-secret", url="https://ewds.example/api", monkeypatch=None,
):
    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.settings = {}
    c.store = MagicMock()
    c.store.field_exists.return_value = False

    def fake_get_setting(section, key, default=None):
        if section == "data_collector" and key == "datasources":
            return {"glofas_ews": url}
        if section == "common" and key == "workdir":
            return workdir
        return default

    c.config = MagicMock()
    c.config.get_setting.side_effect = fake_get_setting
    if api_key is not None:
        monkeypatch.setenv("GLOFAS_API_KEY", api_key)
    else:
        monkeypatch.delenv("GLOFAS_API_KEY", raising=False)
    return c


# ---- base_url() -------------------------------------------------------------------


def test_base_url_resolves_from_data_collector_datasources_not_own_settings_section():
    """FieldCollectorBase's default base_url() reads self.settings["datasources"],
    which assumes settings_section == "data_collector" -- not true here (settings_section
    is "flood_risk"). drain_backfill() calls base_url() directly, so this must resolve
    correctly regardless."""
    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.settings = {"enabled": True}  # the "flood_risk" section -- no "datasources" key
    c.config = MagicMock()
    c.config.get_setting.side_effect = lambda section, key, default=None: (
        {"glofas_ews": "https://ewds.example/api/"} if (section, key) == ("data_collector", "datasources") else default
    )

    assert c.base_url() == "https://ewds.example/api"  # trailing slash stripped


def test_base_url_returns_none_when_glofas_ews_not_configured():
    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.settings = {"enabled": True}
    c.config = MagicMock()
    c.config.get_setting.return_value = {}

    assert c.base_url() is None


# ---- collect(): credential gating -----------------------------------------------


def test_collect_skips_without_raising_when_no_api_key(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), api_key=None, monkeypatch=monkeypatch)

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)

    mock_client_cls.assert_not_called()
    c.store.store_field.assert_not_called()


def test_collect_skips_without_raising_when_no_glofas_ews_datasource(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), url="", monkeypatch=monkeypatch)

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)

    mock_client_cls.assert_not_called()


# ---- collect(): search-fallback + gives-up shape (mirrors CamsGhgForecastCollector) --


def test_collect_falls_back_to_an_earlier_date_when_todays_run_is_not_yet_published(
    tmp_path, monkeypatch
):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    seen_dates = []

    def fake_retrieve(dataset, request, target):
        seen_dates.append(f"{request['year'][0]}{request['month'][0]}{request['day'][0]}")
        if len(seen_dates) == 1:
            raise RuntimeError("400 Client Error: today's run not published yet")
        # Bare (unarchived) netCDF -- just needs to exist for retrieve_with_fallback's
        # unzip=False path; _process_and_store's own parsing is tested separately below.
        with open(target, "wb") as f:
            f.write(b"placeholder-bytes")

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.flood_risk.cdsapi.Client", return_value=mock_client
    ), patch.object(FloodRiskLiveCollector, "_process_and_store") as mock_process, patch(
        "atmos_gl.collectors.flood_risk.ensure_gumbel_fit_cached",
        return_value="/fake/gumbel-fit.nc",
    ):
        c.collect(ctx=None)

    assert len(seen_dates) == 2
    assert seen_dates[1] < seen_dates[0]  # tried an earlier date second
    mock_process.assert_called_once()
    dest_arg = mock_process.call_args[0][0]
    assert os.path.exists(dest_arg)


def test_collect_gives_up_gracefully_when_no_recent_run_is_available(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = RuntimeError("400 Client Error")

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client", return_value=mock_client):
        c.collect(ctx=None)  # must not raise

    assert mock_client.retrieve.call_count == GLOFAS_SEARCH_DAYS
    assert not os.path.exists(glofas_forecast_cache_path(str(tmp_path)))
    c.store.store_field.assert_not_called()


def test_collect_returns_gracefully_without_raising_on_timeout(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client"), patch(
        "atmos_gl.lib.cds_client.retrieve_with_timeout",
        side_effect=concurrent.futures.TimeoutError,
    ):
        c.collect(ctx=None)  # must not raise

    assert not os.path.exists(glofas_forecast_cache_path(str(tmp_path)))


def test_collect_skips_the_fetch_when_the_latest_run_is_already_fully_stored(
    tmp_path, monkeypatch
):
    """GloFAS publishes once per day -- collect() must not re-fetch a run whose every
    leadtime day is already in the field catalog."""
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    dest = glofas_forecast_cache_path(str(tmp_path))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    _write_forecast_fixture(
        dest, run_date="2026-08-29", leadtime_hours=[24],
        lat=[10.0], lon=[30.0], ensemble_by_leadtime=[np.full((5, 1, 1), 100.0)],
    )
    c.store.field_exists.return_value = True  # last leadtime already stored

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)

    mock_client_cls.assert_not_called()


# ---- _process_and_store: real (tiny) netCDF fixtures ----------------------------


def _write_forecast_fixture(path, run_date, leadtime_hours, lat, lon, ensemble_by_leadtime):
    """ensemble_by_leadtime: list of (n_members, len(lat), len(lon)) arrays, one per
    leadtime hour, matching cems-glofas-forecast's real dim order/names (confirmed
    live during issue #371's spike: number, forecast_period,
    forecast_reference_time, latitude, longitude)."""
    stacked = np.stack(ensemble_by_leadtime, axis=1)[:, :, np.newaxis, :, :]
    n_members = ensemble_by_leadtime[0].shape[0]
    ds = xr.Dataset(
        {
            "dis24": (
                ("number", "forecast_period", "forecast_reference_time", "latitude", "longitude"),
                stacked,
            )
        },
        coords={
            "number": np.arange(1, n_members + 1),
            "forecast_period": [np.timedelta64(h, "h") for h in leadtime_hours],
            "forecast_reference_time": [np.datetime64(run_date)],
            "latitude": np.asarray(lat, dtype=np.float64),
            "longitude": np.asarray(lon, dtype=np.float64),
        },
    )
    ds.to_netcdf(path)


def _write_gumbel_fixture(path, lat, lon, loc_value, scale_value):
    loc = np.full((len(lat), len(lon)), loc_value, dtype=np.float64)
    scale = np.full((len(lat), len(lon)), scale_value, dtype=np.float64)
    ds = xr.Dataset(
        {
            "loc": (("latitude", "longitude"), loc),
            "scale": (("latitude", "longitude"), scale),
        },
        coords={
            "latitude": np.asarray(lat, dtype=np.float64),
            "longitude": np.asarray(lon, dtype=np.float64),
        },
    )
    ds.to_netcdf(path)


def test_process_and_store_classifies_each_cell_and_stores_one_field_per_leadtime(tmp_path):
    lat, lon = [20.0, 10.0], [30.0, 40.0]  # descending lat, matching real GloFAS grids
    loc, scale = 500.0, 100.0
    q20 = gumbel_threshold_discharge(20.0, loc, scale)
    q2 = gumbel_threshold_discharge(2.0, loc, scale)

    # cell (0,0): every member well above the 20yr threshold -> band 3
    # every other cell: every member well below even the 2yr threshold -> band 0
    ensemble_f24 = np.full((10, 2, 2), q2 - 1000.0)
    ensemble_f24[:, 0, 0] = q20 + 1000.0
    ensemble_f48 = ensemble_f24.copy()

    forecast_path = str(tmp_path / "forecast.nc")
    gumbel_path = str(tmp_path / "gumbel.nc")
    _write_forecast_fixture(
        forecast_path, run_date="2026-08-29", leadtime_hours=[24, 48],
        lat=lat, lon=lon, ensemble_by_leadtime=[ensemble_f24, ensemble_f48],
    )
    _write_gumbel_fixture(gumbel_path, lat=lat, lon=lon, loc_value=loc, scale_value=scale)

    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.store = MagicMock()

    c._process_and_store(forecast_path, gumbel_path)

    assert c.store.store_field.call_count == 2
    first_call = c.store.store_field.call_args_list[0]
    run_date_str, run_id, fhour, product, unpacked, valid_time = first_call[0]

    assert run_date_str == "20260829"
    assert run_id == "00"
    assert fhour == 24
    assert product == "flood_risk_live"
    assert unpacked["values"][0, 0] == 3  # 20yr band
    assert unpacked["values"][1, 1] == 0  # below every threshold
    assert unpacked["values2"][0, 0] == pytest.approx(1.0)  # 100% of members exceed
    assert valid_time == datetime(2026, 8, 29, tzinfo=timezone.utc) + timedelta(hours=24)

    second_call = c.store.store_field.call_args_list[1]
    assert second_call[0][2] == 48  # fhour


# ---- data_status() ---------------------------------------------------------------


def make_bare_data_status_collector(settings=None):
    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.status_name = "flood_risk_live"
    c.settings = settings or {"enabled": True}
    c.process_status_adapter = MagicMock()
    c.store = MagicMock()
    return c


def test_data_status_no_catalog_data_yet():
    c = make_bare_data_status_collector()
    c.process_status_adapter.get_process_status.return_value = None
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

    result = c.data_status()

    assert result["name"] == "flood_risk_live"
    assert result["percent"] == 0.0
    assert result["last_updated"] is None


def test_data_status_percent_reflects_fraction_of_the_7_expected_leadtime_days():
    c = make_bare_data_status_collector()
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None, "last_error": None,
    }
    # Only 3 of the 7 expected leadtime days (24, 48, 72) present.
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": "20260829", "run_id": "00", "hours": [24, 48, 72],
    }

    result = c.data_status()

    # build_status() rounds percent to 1 decimal place.
    assert result["percent"] == pytest.approx(round(300.0 / 7.0, 1))
    assert "3/7 leadtime day(s)" in result["detail"]


def test_data_status_full_coverage_reports_100_percent():
    c = make_bare_data_status_collector()
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None, "last_error": None,
    }
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": "20260829", "run_id": "00",
        "hours": [int(h) for h in GLOFAS_LEADTIME_HOURS],
    }

    result = c.data_status()

    assert result["percent"] == 100.0


def test_data_status_last_error_takes_priority_over_computed_detail():
    c = make_bare_data_status_collector()
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None, "last_error": "EWDS unreachable",
    }
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

    result = c.data_status()

    assert result["detail"] == "EWDS unreachable"
