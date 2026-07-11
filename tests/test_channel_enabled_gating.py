#!/usr/bin/env python3
"""Tests for the data_collector.channel_enabled per-source opt-out (wayfinder map #106,
resolves #111): a collector whose channel is disabled must be skipped entirely by both
driving loops -- _drive() (event feeds/file caches) and CollectorService._collect_fields()
(the three FieldCollectorBase subclasses) -- without even touching is_stale()/
has_new_data(), since those can themselves hit the network (e.g. a HEAD request).
"""
from unittest.mock import MagicMock, patch

from atmos_gl.collectors import _drive
from atmos_gl.collectors.base import CollectorBase
from atmos_gl.collectors.service import CollectorService


def make_fake_collector_class(section, channel_key=None):
    """A minimal CollectorBase-shaped class whose is_stale/has_new_data/collect are
    spies (appending to a shared list), so tests can assert whether _drive() ever
    called them at all."""
    calls = []

    class FakeCollector(CollectorBase):
        pass

    FakeCollector.section = section
    FakeCollector.channel_key = channel_key
    FakeCollector.period_s = 60.0

    def __init__(self, config):
        self.config = config

    def is_stale(self, last_run):
        calls.append("is_stale")
        return True

    def has_new_data(self):
        calls.append("has_new_data")
        return True

    def collect(self):
        calls.append("collect")

    FakeCollector.__init__ = __init__
    FakeCollector.is_stale = is_stale
    FakeCollector.has_new_data = has_new_data
    FakeCollector.collect = collect
    return FakeCollector, calls


def make_fake_config(channel_enabled):
    config = MagicMock()
    config.get_setting.side_effect = (
        lambda section, key, default=None: channel_enabled
        if (section, key) == ("data_collector", "channel_enabled")
        else default
    )
    return config


@patch("atmos_gl.collectors.ProcessStatusAdapter")
def test_drive_skips_a_collector_whose_channel_is_disabled(mock_psa):
    FakeCls, calls = make_fake_collector_class("quakes", channel_key="quakes")
    config = make_fake_config({"quakes": False})

    _drive([FakeCls], config, {})

    assert calls == []


@patch("atmos_gl.collectors.ProcessStatusAdapter")
def test_drive_runs_normally_when_channel_is_enabled(mock_psa):
    FakeCls, calls = make_fake_collector_class("quakes", channel_key="quakes")
    config = make_fake_config({"quakes": True})

    _drive([FakeCls], config, {})

    assert calls == ["is_stale", "has_new_data", "collect"]


@patch("atmos_gl.collectors.ProcessStatusAdapter")
def test_drive_runs_normally_when_channel_key_absent_from_dict(mock_psa):
    """A channel not yet present in channel_enabled defaults to enabled -- an operator
    on an older config (or one edited by hand) shouldn't silently lose data."""
    FakeCls, calls = make_fake_collector_class("quakes", channel_key="quakes")
    config = make_fake_config({})

    _drive([FakeCls], config, {})

    assert "collect" in calls


@patch("atmos_gl.collectors.ProcessStatusAdapter")
def test_drive_ignores_channel_enabled_for_a_collector_with_no_channel_key(mock_psa):
    """storms/markers aren't part of the channel_enabled feature -- channel_key stays
    None, so they always run regardless of what's in the dict."""
    FakeCls, calls = make_fake_collector_class("storms", channel_key=None)
    config = make_fake_config({"storms": False})

    _drive([FakeCls], config, {})

    assert "collect" in calls


@patch("atmos_gl.collectors.ProcessStatusAdapter")
def test_drive_does_not_advance_last_runs_for_a_disabled_channel(mock_psa):
    """Re-enabling a channel should trigger immediate collection, not wait out an
    already-ticking period -- confirmed by last_runs staying untouched while disabled."""
    FakeCls, _ = make_fake_collector_class("quakes", channel_key="quakes")
    config = make_fake_config({"quakes": False})
    last_runs = {}

    _drive([FakeCls], config, last_runs)

    assert last_runs == {}


def make_fake_field_collector_class(status_name):
    calls = []

    class FakeFieldCollector:
        pass

    def __init__(self, config, store):
        self.config = config
        self.store = store

    def collect(self, ctx):
        calls.append(status_name)

    FakeFieldCollector.status_name = status_name
    FakeFieldCollector.__init__ = __init__
    FakeFieldCollector.collect = collect
    return FakeFieldCollector, calls


def make_bare_service(config):
    svc = CollectorService.__new__(CollectorService)
    svc.config = config
    svc.store = MagicMock()
    svc.process_status_adapter = MagicMock()
    return svc


def test_collect_fields_skips_disabled_and_runs_enabled_field_collectors():
    gfs_atmos_cls, gfs_atmos_calls = make_fake_field_collector_class("gfs_atmos")
    gfs_waves_cls, gfs_waves_calls = make_fake_field_collector_class("gfs_waves")
    config = make_fake_config({"gfs_atmos": False, "gfs_waves": True})
    svc = make_bare_service(config)

    with patch(
        "atmos_gl.collectors.service.FIELD_COLLECTOR_CLASSES",
        (gfs_atmos_cls, gfs_waves_cls),
    ):
        svc._collect_fields()

    assert gfs_atmos_calls == []
    assert gfs_waves_calls == ["gfs_waves"]


def test_collect_fields_runs_all_when_channel_enabled_missing_entirely():
    gfs_atmos_cls, gfs_atmos_calls = make_fake_field_collector_class("gfs_atmos")
    config = make_fake_config({})
    svc = make_bare_service(config)

    with patch("atmos_gl.collectors.service.FIELD_COLLECTOR_CLASSES", (gfs_atmos_cls,)):
        svc._collect_fields()

    assert gfs_atmos_calls == ["gfs_atmos"]
