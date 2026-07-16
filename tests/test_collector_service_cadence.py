#!/usr/bin/env python3
"""Tests for CollectorService.refresh_settings()'s cadence derivation, migrated from
data_collector.update_minutes/update_hours onto runs_per_day (same
period_s_from_runs_per_day() formula every other collector uses -- see
lib/data_status.py). FieldCollectorBase._service_period_s() mirrors the same formula
for data_status()'s next_update display; covered here too since both must stay in
sync with each other.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.service import CollectorService
from atmos_gl.collectors.field_base import FieldCollectorBase


def make_bare_service(settings):
    svc = CollectorService.__new__(CollectorService)
    svc.config = MagicMock()
    svc.config.get_section.return_value = settings
    return svc


def test_refresh_settings_derives_period_from_runs_per_day():
    svc = make_bare_service({"runs_per_day": 24})
    svc.refresh_settings()
    assert svc.update_period_s == 3600.0


def test_refresh_settings_defaults_to_96_runs_per_day_matching_the_old_15_minute_default():
    svc = make_bare_service({})
    svc.refresh_settings()
    assert svc.update_period_s == 900.0


def test_refresh_settings_ignores_legacy_update_minutes_and_update_hours():
    """These keys are retired -- a config file that still has them (pre-migration)
    must not affect the computed cadence at all."""
    svc = make_bare_service({"update_minutes": 5, "update_hours": 1, "runs_per_day": 6})
    svc.refresh_settings()
    assert svc.update_period_s == 86400.0 / 6


def make_bare_field_collector(settings):
    c = FieldCollectorBase.__new__(FieldCollectorBase)
    c.settings = settings
    return c


def test_service_period_s_matches_refresh_settings_formula():
    """FieldCollectorBase._service_period_s() must mirror
    CollectorService.refresh_settings()'s formula exactly, since both read
    data_collector.runs_per_day and must agree on the real cadence."""
    svc = make_bare_service({"runs_per_day": 48})
    svc.refresh_settings()
    c = make_bare_field_collector({"runs_per_day": 48})
    assert c._service_period_s() == svc.update_period_s


def test_service_period_s_defaults_to_96_runs_per_day():
    c = make_bare_field_collector({})
    assert c._service_period_s() == 900.0
