#!/usr/bin/env python3
"""Tests for SingleFileFieldCollector -- the collect()/backfill_hour() shared by
GfsWavesCollector and RtofsCurrentsCollector (architecture review candidate "collapse
the field-collector download -> unpack -> store mechanic"). GfsAtmosCollector's
multi-product byte-range shape is untouched and not covered here.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.field_base import SingleFileFieldCollector, CycleContext


def make_collector(resolve_url=None, guard_cycle=None, cache_hours=4, cleanup_idx=False):
    c = SingleFileFieldCollector.__new__(SingleFileFieldCollector)
    c.status_name = "test_source"
    c.datasource_key = "test"
    c.baseline_key = "test"
    c.products = {"widget": lambda tmp_path: {"tmp_path": tmp_path}}
    c.tempfile_suffix = ".bin"
    c.cleanup_idx = cleanup_idx
    c.settings = {"cache_hours": cache_hours}
    c.store = MagicMock()
    c.store.field_exists.return_value = False
    c.base_url = MagicMock(return_value="https://example.test")
    if resolve_url is not None:
        c._resolve_download_url = resolve_url
    if guard_cycle is not None:
        c._guard_cycle = guard_cycle
    return c


@patch("atmos_gl.collectors.field_base.download_whole")
def test_collect_skips_hours_where_field_exists(mock_download):
    run_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    baseline = {"date_str": "2026-07-15", "run": "00", "timestamp": run_ts}

    c = make_collector(resolve_url=MagicMock(return_value="https://example.test/f002"))
    c.resolve_baseline = MagicMock(return_value=baseline)
    c.store.field_exists.return_value = True

    c.collect(CycleContext())

    mock_download.assert_not_called()
    c.store.store_field.assert_not_called()


@patch("atmos_gl.collectors.field_base.download_whole")
def test_collect_downloads_unpacks_and_stores_missing_hours(mock_download):
    run_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    baseline = {"date_str": "2026-07-15", "run": "00", "timestamp": run_ts}
    mock_download.return_value = b"payload"

    c = make_collector(
        resolve_url=MagicMock(return_value="https://example.test/f001"), cache_hours=1
    )
    c.resolve_baseline = MagicMock(return_value=baseline)

    c.collect(CycleContext())

    assert c.store.store_field.call_count == 1
    run_date_str, run_id, fhour, product, fields, valid = c.store.store_field.call_args[0]
    assert (run_date_str, run_id, product) == ("2026-07-15", "00", "widget")
    assert fields["tmp_path"].endswith(".bin")  # unpacker ran on a real tempfile path


def test_collect_aborts_when_guard_cycle_returns_false():
    baseline = {
        "date_str": "2026-07-15",
        "run": "00",
        "timestamp": datetime.now(timezone.utc),
    }
    resolve_url = MagicMock()
    c = make_collector(resolve_url=resolve_url, guard_cycle=MagicMock(return_value=False))
    c.resolve_baseline = MagicMock(return_value=baseline)

    c.collect(CycleContext())

    resolve_url.assert_not_called()
    c.store.store_field.assert_not_called()


def test_collect_only_allows_fallback_on_the_first_hour():
    run_ts = datetime.now(timezone.utc)
    baseline = {"date_str": "2026-07-15", "run": "00", "timestamp": run_ts}
    resolve_url = MagicMock(return_value=None)  # no downloads triggered; just inspect calls
    c = make_collector(resolve_url=resolve_url, cache_hours=3)
    c.resolve_baseline = MagicMock(return_value=baseline)

    c.collect(CycleContext())

    calls = resolve_url.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs["allow_fallback"] is True
    assert calls[1].kwargs["allow_fallback"] is False
    assert calls[2].kwargs["allow_fallback"] is False


@patch("atmos_gl.collectors.field_base.download_whole")
def test_backfill_hour_always_allows_fallback(mock_download):
    mock_download.return_value = b"payload"
    resolve_url = MagicMock(return_value="https://example.test/f005")
    c = make_collector(resolve_url=resolve_url)

    ok = c.backfill_hour("2026-07-15", "00", 5, "widget")

    assert ok is True
    resolve_url.assert_called_once_with(
        "https://example.test", "2026-07-15", "00", 5, allow_fallback=True
    )


def test_backfill_hour_returns_false_when_url_unavailable():
    c = make_collector(resolve_url=MagicMock(return_value=None))

    ok = c.backfill_hour("2026-07-15", "00", 5, "widget")

    assert ok is False
