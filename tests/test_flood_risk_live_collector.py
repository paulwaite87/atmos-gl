#!/usr/bin/env python3
"""FloodRiskLiveCollector: GloFAS ensemble discharge forecast classified per grid
cell against ETH's Gumbel-fit return-period thresholds (see issue #371).

Mirrors test_greenhouse_gases_forecast_collector.py's seam (mock the cdsapi.Client
boundary, assert on cache/store calls, not on real network access) for the
credential/search-fallback tests, and test_field_collector_base_data_status.py's
bare-collector construction for data_status(). _process_and_store_one_hour is
exercised against tiny REAL netCDF fixtures (written via xarray) since its job is
genuinely parsing/regridding/classifying array data, not just orchestrating a fetch.

collect() fetches/stores each of GLOFAS_LEADTIME_HOURS as its own request (one
netCDF per hour, not all 7 in one job) -- see the collector's own docstring for why.
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
    load_gumbel_fit,
)


def make_bare_live_collector(
    workdir=".", api_key="glofas-secret", url="https://ewds.example/api", monkeypatch=None,
):
    # collect()'s self-gate (see its own comment) lives on the CLASS, not the instance,
    # since production relies on it surviving fresh-instance construction every cycle --
    # reset it here so one test's attempt doesn't silently gate the next.
    FloodRiskLiveCollector._last_attempt_monotonic = None
    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.settings = {}
    c.store = MagicMock()
    c.store.field_exists.return_value = False
    # Cold start (no resumable run) by default -- _resume_run_date_str's own tests
    # cover the resume path directly.
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

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
    """The FIRST leadtime hour's search-fallback shape: an unavailable freshest
    candidate date is skipped in favour of an earlier one."""
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    seen_dates = []

    def fake_retrieve(dataset, request, target):
        seen_dates.append(f"{request['year'][0]}{request['month'][0]}{request['day'][0]}")
        if len(seen_dates) == 1:
            raise RuntimeError("400 Client Error: today's run not published yet")
        # Bare (unarchived) netCDF -- just needs to exist for retrieve_with_fallback's
        # unzip=False path; _process_and_store_one_hour's own parsing is tested
        # separately below.
        with open(target, "wb") as f:
            f.write(b"placeholder-bytes")

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.flood_risk.cdsapi.Client", return_value=mock_client
    ), patch.object(
        FloodRiskLiveCollector, "_process_and_store_one_hour",
        return_value=("20260828", 24),
    ) as mock_process, patch(
        "atmos_gl.collectors.flood_risk.ensure_gumbel_fit_cached",
        return_value="/fake/gumbel-fit.nc",
    ), patch(
        "atmos_gl.collectors.flood_risk.load_gumbel_fit",
        return_value=(None, None, None, None),
    ):
        c.collect(ctx=None)

    assert seen_dates[1] < seen_dates[0]  # tried an earlier date second, for hour[0]
    # Only the first leadtime hour needed the candidate search; once locked in, the
    # remaining 6 hours each fetch a single, already-known date -- one retrieve() per
    # hour (7 total) plus the one extra failed candidate for the first hour.
    assert mock_client.retrieve.call_count == len(GLOFAS_LEADTIME_HOURS) + 1
    assert mock_process.call_count == len(GLOFAS_LEADTIME_HOURS)
    dest_arg = mock_process.call_args_list[0][0][0]
    assert not os.path.exists(dest_arg)  # cleaned up after processing


def test_collect_gives_up_gracefully_when_no_recent_run_is_available(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = RuntimeError("400 Client Error")

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client", return_value=mock_client):
        c.collect(ctx=None)  # must not raise

    # Only the first leadtime hour's candidate search runs; every candidate fails, so
    # collect() stops there rather than trying the remaining hours.
    assert mock_client.retrieve.call_count == GLOFAS_SEARCH_DAYS
    assert not os.path.exists(glofas_forecast_cache_path(str(tmp_path), GLOFAS_LEADTIME_HOURS[0]))
    c.store.store_field.assert_not_called()


def test_collect_returns_gracefully_without_raising_on_timeout(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client"), patch(
        "atmos_gl.lib.cds_client.retrieve_with_timeout",
        side_effect=concurrent.futures.TimeoutError,
    ):
        c.collect(ctx=None)  # must not raise

    assert not os.path.exists(glofas_forecast_cache_path(str(tmp_path), GLOFAS_LEADTIME_HOURS[0]))


def test_collect_skips_the_fetch_when_the_latest_run_is_already_fully_stored(
    tmp_path, monkeypatch
):
    """GloFAS publishes once per day -- collect() must not re-fetch a run whose every
    leadtime hour is already in the field catalog."""
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    # Recent enough (well within GLOFAS_SEARCH_DAYS) that _resume_run_date_str locks
    # onto it rather than treating it as stale.
    recent_date_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": recent_date_str, "run_id": "00",
        "hours": [int(h) for h in GLOFAS_LEADTIME_HOURS],
    }
    c.store.field_exists.return_value = True  # every leadtime hour already stored

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)

    mock_client_cls.assert_not_called()


def test_collect_resumes_only_the_missing_hours_of_a_partially_stored_run(
    tmp_path, monkeypatch
):
    """A run interrupted mid-fetch (connection drop, OOM, restart) must resume on
    exactly the hours still missing, without re-searching candidate dates or
    re-fetching hours already safely in the catalog."""
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    recent_date_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = {
        "run_date": recent_date_str, "run_id": "00", "hours": [24],
    }
    stored_hours = {24}
    c.store.field_exists.side_effect = (
        lambda run_date, run_id, fhour, product: fhour in stored_hours
    )

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(b"placeholder-bytes")

    mock_client = MagicMock()
    mock_client.retrieve.side_effect = fake_retrieve

    with patch(
        "atmos_gl.collectors.flood_risk.cdsapi.Client", return_value=mock_client
    ), patch.object(
        FloodRiskLiveCollector, "_process_and_store_one_hour",
        return_value=(recent_date_str, 48),
    ), patch(
        "atmos_gl.collectors.flood_risk.ensure_gumbel_fit_cached",
        return_value="/fake/gumbel-fit.nc",
    ), patch(
        "atmos_gl.collectors.flood_risk.load_gumbel_fit",
        return_value=(None, None, None, None),
    ):
        c.collect(ctx=None)

    # One request per still-missing hour (6 of the 7), each for the ALREADY-KNOWN
    # date -- no candidate-date search re-run.
    assert mock_client.retrieve.call_count == len(GLOFAS_LEADTIME_HOURS) - 1
    for call in mock_client.retrieve.call_args_list:
        request = call[0][1]
        assert (
            f"{request['year'][0]}{request['month'][0]}{request['day'][0]}"
            == recent_date_str
        )


# ---- collect(): self-gate on flood_risk.runs_per_day -----------------------------


def test_collect_skips_when_called_again_before_the_configured_period_has_elapsed(
    tmp_path, monkeypatch
):
    """FieldCollectorDriver has no is_stale() cadence check of its own (unlike
    EventFeedDriver) -- see collect()'s own comment -- so this collector must self-gate
    on flood_risk.runs_per_day, or a slow/failing EWDS request gets retried every single
    service cycle instead of waiting out its own period (confirmed live on prod: repeated
    requests fired every ~15-30min cycle, racing/aborting each other's downloads)."""
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    c.settings = {"runs_per_day": 24}  # period_s = 3600

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)
    assert mock_client_cls.call_count == 1

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls2:
        c.collect(ctx=None)  # immediately again -- must be gated, not re-attempted
    mock_client_cls2.assert_not_called()


def test_collect_retries_once_the_configured_period_has_elapsed(tmp_path, monkeypatch):
    c = make_bare_live_collector(workdir=str(tmp_path), monkeypatch=monkeypatch)
    c.settings = {"runs_per_day": 24}  # period_s = 3600

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls:
        c.collect(ctx=None)
    assert mock_client_cls.call_count == 1

    FloodRiskLiveCollector._last_attempt_monotonic -= 3601  # simulate period_s elapsed

    with patch("atmos_gl.collectors.flood_risk.cdsapi.Client") as mock_client_cls2:
        c.collect(ctx=None)
    assert mock_client_cls2.call_count == 1


# ---- _process_and_store_one_hour: real (tiny) netCDF fixtures -------------------


def _write_forecast_fixture(
    path, run_date, leadtime_hour, lat, lon, ensemble, drop_forecast_period_dim=False
):
    """One leadtime hour's (n_members, len(lat), len(lon)) ensemble, matching
    cems-glofas-forecast's real dim order/names for a single-leadtime_hour request
    (confirmed live during issue #371's spike for the multi-leadtime shape: number,
    forecast_period, forecast_reference_time, latitude, longitude).

    `drop_forecast_period_dim` covers the other plausible shape a single-value
    leadtime_hour request could come back as -- forecast_period present only as a
    scalar coordinate, not a dimension -- since _process_and_store_one_hour must
    handle both (see its own "if 'forecast_period' in ds.dims" guard)."""
    n_members = ensemble.shape[0]
    if drop_forecast_period_dim:
        data = ensemble[:, np.newaxis, :, :]
        dims = ("number", "forecast_reference_time", "latitude", "longitude")
        coords = {
            "number": np.arange(1, n_members + 1),
            "forecast_period": np.timedelta64(leadtime_hour, "h"),
            "forecast_reference_time": [np.datetime64(run_date)],
            "latitude": np.asarray(lat, dtype=np.float64),
            "longitude": np.asarray(lon, dtype=np.float64),
        }
    else:
        data = ensemble[:, np.newaxis, np.newaxis, :, :]
        dims = ("number", "forecast_period", "forecast_reference_time", "latitude", "longitude")
        coords = {
            "number": np.arange(1, n_members + 1),
            "forecast_period": [np.timedelta64(leadtime_hour, "h")],
            "forecast_reference_time": [np.datetime64(run_date)],
            "latitude": np.asarray(lat, dtype=np.float64),
            "longitude": np.asarray(lon, dtype=np.float64),
        }
    ds = xr.Dataset({"dis24": (dims, data)}, coords=coords)
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


@pytest.mark.parametrize("drop_forecast_period_dim", [False, True])
def test_process_and_store_one_hour_classifies_each_cell_and_stores_the_field(
    tmp_path, drop_forecast_period_dim
):
    lat, lon = [20.0, 10.0], [30.0, 40.0]  # descending lat, matching real GloFAS grids
    loc, scale = 500.0, 100.0
    q20 = gumbel_threshold_discharge(20.0, loc, scale)
    q2 = gumbel_threshold_discharge(2.0, loc, scale)

    # cell (0,0): every member well above the 20yr threshold -> band 3
    # every other cell: every member well below even the 2yr threshold -> band 0
    ensemble = np.full((10, 2, 2), q2 - 1000.0)
    ensemble[:, 0, 0] = q20 + 1000.0

    forecast_path = str(tmp_path / "forecast_f024.nc")
    gumbel_path = str(tmp_path / "gumbel.nc")
    _write_forecast_fixture(
        forecast_path, run_date="2026-08-29", leadtime_hour=24,
        lat=lat, lon=lon, ensemble=ensemble,
        drop_forecast_period_dim=drop_forecast_period_dim,
    )
    _write_gumbel_fixture(gumbel_path, lat=lat, lon=lon, loc_value=loc, scale_value=scale)
    loc_native, scale_native, gumbel_lat, gumbel_lon = load_gumbel_fit(gumbel_path)

    c = FloodRiskLiveCollector.__new__(FloodRiskLiveCollector)
    c.store = MagicMock()

    run_date_str, fhour = c._process_and_store_one_hour(
        forecast_path, loc_native, scale_native, gumbel_lat, gumbel_lon
    )

    assert run_date_str == "20260829"
    assert fhour == 24
    c.store.store_field.assert_called_once()
    call_run_date_str, run_id, call_fhour, product, unpacked, valid_time = (
        c.store.store_field.call_args[0]
    )

    assert call_run_date_str == "20260829"
    assert run_id == "00"
    assert call_fhour == 24
    assert product == "flood_risk_live"
    assert unpacked["values"][0, 0] == 3  # 20yr band
    assert unpacked["values"][1, 1] == 0  # below every threshold
    assert unpacked["values2"][0, 0] == pytest.approx(1.0)  # 100% of members exceed
    assert valid_time == datetime(2026, 8, 29, tzinfo=timezone.utc) + timedelta(hours=24)


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


def test_data_status_next_update_ignores_the_frontend_show_toggle():
    """Regression guard: settings_section is overridden to the shared "flood_risk"
    section (same convention as CACHE_COLLECTORS), so self.enabled here reflects the
    layer's frontend Show-toggle, not a real collection kill-switch -- _collect_fields()
    drives this collector every cycle unconditionally of it. next_update must stay
    populated (matching freshness_data_status()'s next_update_respects_enabled=False
    default) even while the toggle is off, while the returned "enabled" field still
    correctly reports the real flag value."""
    c = make_bare_data_status_collector(settings={"enabled": False})
    c.process_status_adapter.get_process_status.return_value = {
        "last_updated": None, "last_error": None,
    }
    c.store.field_catalog_adapter.get_latest_run_hours.return_value = None

    result = c.data_status()

    assert result["enabled"] is False
    assert result["next_update"] is not None
