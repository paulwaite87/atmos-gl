#!/usr/bin/env python3
"""Tests for lib/data_status.py (architecture review candidate "one status module").
freshness_percent/estimate_next_update/period_s_from_runs_per_day/read_process_status/
build_status replace the same logic hand-duplicated across CollectorBase.data_status(),
AsyncCollectorBase.data_status(), FieldCollectorBase.data_status() and
Updater.layer_status() -- none of it had any test coverage before this.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from atmos_gl.lib.data_status import (
    freshness_percent,
    estimate_next_update,
    period_s_from_runs_per_day,
    read_process_status,
    resolve_run_epoch_utc,
    build_status,
    resolve_datasource_url,
    resolve_source_url,
    freshness_data_status,
    RUNS_PER_DAY_CHOICES,
)


# --- resolve_run_epoch_utc (shared by routes/config.py's scrubber timeline,
# FieldCollectorBase's coverage math, and Updater.layer_status()'s now-onward
# filtering -- three previously-independent implementations of this same date math) ---


def test_resolve_run_epoch_utc_accepts_yyyymmdd_string():
    result = resolve_run_epoch_utc("20260613", "18")
    assert result == datetime(2026, 6, 13, 18, 0, 0, tzinfo=timezone.utc)


def test_resolve_run_epoch_utc_accepts_iso_date_string():
    result = resolve_run_epoch_utc("2026-06-13", "18")
    assert result == datetime(2026, 6, 13, 18, 0, 0, tzinfo=timezone.utc)


def test_resolve_run_epoch_utc_accepts_a_date_object():
    result = resolve_run_epoch_utc(date(2026, 6, 13), "6")
    assert result == datetime(2026, 6, 13, 6, 0, 0, tzinfo=timezone.utc)


def test_resolve_run_epoch_utc_accepts_a_datetime_object():
    result = resolve_run_epoch_utc(datetime(2026, 6, 13, 9, 30), "0")
    assert result == datetime(2026, 6, 13, 0, 0, 0, tzinfo=timezone.utc)


def test_resolve_run_epoch_utc_accepts_int_run_id():
    result = resolve_run_epoch_utc("20260613", 12)
    assert result == datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


# --- freshness_percent ---


def test_freshness_percent_is_zero_when_never_updated():
    assert freshness_percent(None, 3600) == 0.0


def test_freshness_percent_is_100_immediately_after_update():
    now = datetime.now(timezone.utc)
    assert freshness_percent(now, 3600) == 100.0


def test_freshness_percent_is_100_until_period_elapses():
    last_updated = datetime.now(timezone.utc) - timedelta(seconds=3599)
    assert freshness_percent(last_updated, 3600) == 100.0


def test_freshness_percent_decays_linearly_once_overdue():
    # Overdue by exactly one extra period_s -> fully decayed to 0.
    last_updated = datetime.now(timezone.utc) - timedelta(seconds=7200)
    assert freshness_percent(last_updated, 3600) == 0.0


def test_freshness_percent_midway_through_decay():
    # period_s=3600, overdue by 1800s (half of period_s past due) -> ~50%.
    last_updated = datetime.now(timezone.utc) - timedelta(seconds=5400)
    result = freshness_percent(last_updated, 3600)
    assert 45.0 < result < 55.0


def test_freshness_percent_handles_naive_datetimes():
    naive = datetime.now() - timedelta(seconds=10)
    assert freshness_percent(naive, 3600) == 100.0


# --- estimate_next_update ---


def test_estimate_next_update_none_when_disabled():
    assert estimate_next_update(datetime.now(timezone.utc), 3600, False) is None


def test_estimate_next_update_estimates_from_now_when_never_run():
    result = estimate_next_update(None, 3600, True)
    expected = datetime.now(timezone.utc) + timedelta(seconds=3600)
    assert abs((result - expected).total_seconds()) < 2


def test_estimate_next_update_is_precise_when_last_updated_known():
    last_updated = datetime.now(timezone.utc) - timedelta(seconds=100)
    result = estimate_next_update(last_updated, 3600, True)
    assert result == last_updated + timedelta(seconds=3600)


def test_estimate_next_update_handles_naive_datetimes():
    naive = datetime.now() - timedelta(seconds=100)
    result = estimate_next_update(naive, 3600, True)
    assert result.tzinfo is not None


# --- period_s_from_runs_per_day ---


def test_period_s_from_runs_per_day_basic():
    assert period_s_from_runs_per_day(24) == 3600.0
    assert period_s_from_runs_per_day(1) == 86400.0


def test_period_s_from_runs_per_day_defaults_to_1_when_falsy():
    assert period_s_from_runs_per_day(0) == period_s_from_runs_per_day(1)
    assert period_s_from_runs_per_day(None) == period_s_from_runs_per_day(1)


def test_period_s_from_runs_per_day_100_per_day():
    assert period_s_from_runs_per_day(100) == 864.0


def test_period_s_from_runs_per_day_floors_tiny_values_at_001():
    # A near-zero runs_per_day shouldn't produce a runaway-large period.
    assert period_s_from_runs_per_day(0.0001) == 86400.0 / 0.01


def test_runs_per_day_choices_max_value_matches_15_minute_cadence():
    """96/day is data_collector's default, matching its historical 15-minute
    update_minutes cadence exactly."""
    assert RUNS_PER_DAY_CHOICES[-1] == 96
    assert period_s_from_runs_per_day(96) == 900.0


# --- read_process_status ---


def test_read_process_status_returns_none_none_none_when_no_row():
    adapter = MagicMock()
    adapter.get_process_status.return_value = None
    assert read_process_status(adapter, "quakes") == (None, None, None)


def test_read_process_status_extracts_last_updated_last_error_and_status():
    adapter = MagicMock()
    now = datetime.now(timezone.utc)
    adapter.get_process_status.return_value = {
        "last_updated": now, "last_error": "boom", "status": "failed",
    }
    assert read_process_status(adapter, "quakes") == (now, "boom", "failed")
    adapter.get_process_status.assert_called_once_with("quakes")


def test_read_process_status_defaults_status_to_none_when_row_lacks_it():
    """Defensive: a row shape without a "status" key (e.g. a Fake predating this
    field) shouldn't raise -- status just reads as None."""
    adapter = MagicMock()
    now = datetime.now(timezone.utc)
    adapter.get_process_status.return_value = {"last_updated": now, "last_error": None}
    assert read_process_status(adapter, "quakes") == (now, None, None)


# --- build_status ---


def test_build_status_assembles_the_expected_shape():
    now = datetime.now(timezone.utc)
    next_update = now + timedelta(hours=1)
    result = build_status(
        name="quakes",
        kind="collector",
        percent=42.567,
        last_updated=now,
        next_update=next_update,
        enabled=True,
        detail=None,
    )
    assert result == {
        "name": "quakes",
        "kind": "collector",
        "percent": 42.6,
        "last_updated": now,
        "next_update": next_update,
        "enabled": True,
        "detail": None,
        "status": None,
    }


def test_build_status_includes_status_when_provided():
    result = build_status(
        name="sst", kind="collector", percent=100.0, last_updated=None,
        next_update=None, enabled=True, detail=None, status="running",
    )
    assert result["status"] == "running"


def test_build_status_rounds_percent_to_one_decimal():
    result = build_status(
        name="x", kind="layer", percent=33.333333, last_updated=None,
        next_update=None, enabled=False, detail="err",
    )
    assert result["percent"] == 33.3


# --- resolve_datasource_url / resolve_source_url (Candidate 1: one shared URL +
# freshness contract for CollectorBase/AsyncCollectorBase) ---


def _config_with_datasources(datasources):
    config = MagicMock()
    config.get_setting.return_value = datasources
    return config


def test_resolve_datasource_url_returns_configured_value_stripped_of_trailing_slash():
    config = _config_with_datasources({"quakes": "https://example.com/quakes/"})
    assert resolve_datasource_url(config, "quakes") == "https://example.com/quakes"
    config.get_setting.assert_called_once_with("data_collector", "datasources", {})


def test_resolve_datasource_url_empty_string_when_key_unconfigured():
    config = _config_with_datasources({})
    assert resolve_datasource_url(config, "quakes") == ""


def test_resolve_source_url_none_when_datasource_key_empty():
    config = _config_with_datasources({"quakes": "https://example.com/quakes"})
    assert resolve_source_url(config, "") is None


def test_resolve_source_url_none_when_key_configured_but_value_missing():
    config = _config_with_datasources({})
    assert resolve_source_url(config, "quakes") is None


def test_resolve_source_url_returns_the_resolved_url():
    config = _config_with_datasources({"quakes": "https://example.com/quakes"})
    assert resolve_source_url(config, "quakes") == "https://example.com/quakes"


# --- freshness_data_status (CollectorBase/AsyncCollectorBase's shared data_status()
# body -- next_update_respects_enabled is the one real divergence between them) ---


def test_freshness_data_status_shape_with_no_row_yet():
    adapter = MagicMock()
    adapter.get_process_status.return_value = None

    result = freshness_data_status(adapter, "quakes", 3600.0, True)

    assert result["name"] == "quakes"
    assert result["kind"] == "collector"
    assert result["percent"] == 0.0
    assert result["last_updated"] is None
    assert result["enabled"] is True
    assert result["next_update"] is not None  # enabled + never run -> estimated


def test_freshness_data_status_reflects_a_fresh_run():
    adapter = MagicMock()
    now = datetime.now(timezone.utc)
    adapter.get_process_status.return_value = {"last_updated": now, "last_error": None}

    result = freshness_data_status(adapter, "quakes", 3600.0, True)

    assert result["percent"] == 100.0
    assert result["last_updated"] == now
    assert result["next_update"] == now + timedelta(hours=1)


def test_freshness_data_status_default_respects_enabled_for_next_update():
    """Default (next_update_respects_enabled=True, AsyncCollectorBase's case): a
    disabled collector's next_update is None -- it's a real kill-switch."""
    adapter = MagicMock()
    adapter.get_process_status.return_value = None

    result = freshness_data_status(adapter, "shipping_collector", 300.0, False)

    assert result["next_update"] is None


def test_freshness_data_status_can_ignore_enabled_for_next_update():
    """next_update_respects_enabled=False (CollectorBase's case): collection is
    unconditional of the frontend `enabled` flag, so next_update is still computed
    even when enabled=False -- it must not report "disabled" for a source that's
    still being collected in the background."""
    adapter = MagicMock()
    adapter.get_process_status.return_value = None

    result = freshness_data_status(
        adapter, "quakes", 3600.0, False, next_update_respects_enabled=False
    )

    assert result["enabled"] is False
    assert result["next_update"] is not None
