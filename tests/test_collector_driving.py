#!/usr/bin/env python3
"""Tests for collectors/driving.py -- CollectorDriver/EventFeedDriver/FieldCollectorDriver
(architecture review candidate "collapse the two collector-family drivers into one
control flow"). _drive() and CollectorService._collect_fields() had zero test coverage
before this extraction (confirmed via codegraph blast-radius); these tests lock the
shared gate/try/record envelope (CollectorDriver.drive()) precisely, plus each family's
own freshness-check shape -- EventFeedDriver's is_stale/has_new_data three-way branch
with last_runs bookkeeping, FieldCollectorDriver's unconditional collect(ctx) with no
external pre-check. Also locks the record_process_start fix: field collectors
previously never showed "running" in the Data Status UI mid-fetch, unlike event feeds
-- this extraction closes that gap deliberately, not as a side effect.
"""
from unittest.mock import MagicMock, patch

import pytest

from atmos_gl.collectors.driving import CollectorDriver, EventFeedDriver, FieldCollectorDriver


def make_config(channel_enabled=None):
    cfg = MagicMock()
    cfg.get_setting.return_value = channel_enabled or {}
    return cfg


# ---------------------------------------------------------------------------
# CollectorDriver (base): the shared gate/try/record envelope
# ---------------------------------------------------------------------------

class _StubDriver(CollectorDriver):
    def __init__(self, config, process_status_adapter=None):
        super().__init__(config, process_status_adapter)
        self.driven = []

    def _gate_key(self, CollectorCls):
        return CollectorCls.gate_key

    def _status_key(self, CollectorCls):
        return CollectorCls.status_key

    def _drive_one(self, CollectorCls):
        self.driven.append(CollectorCls)
        CollectorCls.action()


def test_drive_skips_a_collector_whose_gate_key_is_disabled():
    class _Disabled:
        gate_key = "chan"
        status_key = "disabled_one"
        action = MagicMock()

    driver = _StubDriver(make_config({"chan": False}), MagicMock())
    driver.drive([_Disabled])

    _Disabled.action.assert_not_called()
    assert driver.driven == []


def test_drive_runs_a_collector_whose_gate_key_is_enabled():
    class _Enabled:
        gate_key = "chan"
        status_key = "enabled_one"
        action = MagicMock()

    driver = _StubDriver(make_config({"chan": True}), MagicMock())
    driver.drive([_Enabled])

    _Enabled.action.assert_called_once()


def test_drive_runs_a_collector_with_no_gate_key_regardless_of_channel_enabled():
    class _Ungated:
        gate_key = None
        status_key = "ungated_one"
        action = MagicMock()

    driver = _StubDriver(make_config({}), MagicMock())
    driver.drive([_Ungated])

    _Ungated.action.assert_called_once()


def test_drive_catches_an_exception_and_records_failure_via_status_key():
    class _Boom:
        gate_key = None
        status_key = "boom_one"

        @staticmethod
        def action():
            raise RuntimeError("kaboom")

    adapter = MagicMock()
    driver = _StubDriver(make_config({}), adapter)

    driver.drive([_Boom])  # must not raise

    adapter.record_process_run.assert_called_once_with(
        "boom_one", "collector", success=False, error="kaboom"
    )


def test_drive_one_collector_failing_does_not_abort_the_rest():
    class _Boom:
        gate_key = None
        status_key = "boom"

        @staticmethod
        def action():
            raise RuntimeError("kaboom")

    class _Fine:
        gate_key = None
        status_key = "fine"
        action = MagicMock()

    driver = _StubDriver(make_config({}), MagicMock())
    driver.drive([_Boom, _Fine])

    _Fine.action.assert_called_once()


def test_hooks_raise_not_implemented_on_the_bare_base():
    driver = CollectorDriver.__new__(CollectorDriver)

    with pytest.raises(NotImplementedError):
        driver._gate_key(None)
    with pytest.raises(NotImplementedError):
        driver._status_key(None)
    with pytest.raises(NotImplementedError):
        driver._drive_one(None)


# ---------------------------------------------------------------------------
# EventFeedDriver
# ---------------------------------------------------------------------------

def make_fake_feed_cls(channel_key="chan", section="feed", is_stale=True, has_new_data=True):
    instance = MagicMock()
    instance.is_stale.return_value = is_stale
    instance.has_new_data.return_value = has_new_data
    instance.period_s = 3600.0
    FakeCls = MagicMock(return_value=instance)
    FakeCls.channel_key = channel_key
    FakeCls.section = section
    FakeCls.__name__ = "FakeFeed"
    return FakeCls, instance


def test_event_feed_driver_gate_key_is_channel_key():
    FakeCls, _ = make_fake_feed_cls(channel_key="my_channel")
    driver = EventFeedDriver(make_config(), {})
    assert driver._gate_key(FakeCls) == "my_channel"


def test_event_feed_driver_status_key_is_section():
    FakeCls, _ = make_fake_feed_cls(section="my_section")
    driver = EventFeedDriver(make_config(), {})
    assert driver._status_key(FakeCls) == "my_section"


def test_event_feed_driver_skips_collect_when_not_yet_due():
    FakeCls, instance = make_fake_feed_cls(is_stale=False)
    last_runs = {}
    adapter = MagicMock()
    driver = EventFeedDriver(make_config(), last_runs, adapter)

    driver._drive_one(FakeCls)

    instance.collect.assert_not_called()
    adapter.record_process_run.assert_not_called()
    assert last_runs == {}


def test_event_feed_driver_records_success_without_collecting_when_unchanged():
    FakeCls, instance = make_fake_feed_cls(section="feed", is_stale=True, has_new_data=False)
    last_runs = {}
    adapter = MagicMock()
    driver = EventFeedDriver(make_config(), last_runs, adapter)

    driver._drive_one(FakeCls)

    instance.collect.assert_not_called()
    adapter.record_process_start.assert_not_called()
    adapter.record_process_run.assert_called_once_with("feed", "collector", success=True)
    assert "feed" in last_runs


def test_event_feed_driver_calls_start_then_collect_then_record_in_order():
    FakeCls, instance = make_fake_feed_cls(section="feed", is_stale=True, has_new_data=True)
    adapter = MagicMock()
    driver = EventFeedDriver(make_config(), {}, adapter)
    manager = MagicMock()
    manager.attach_mock(adapter.record_process_start, "record_process_start")
    manager.attach_mock(instance.collect, "collect")
    manager.attach_mock(adapter.record_process_run, "record_process_run")

    driver._drive_one(FakeCls)

    assert [c[0] for c in manager.mock_calls] == [
        "record_process_start", "collect", "record_process_run",
    ]
    adapter.record_process_run.assert_called_once_with("feed", "collector", success=True)


def test_event_feed_driver_updates_last_runs_after_collecting():
    FakeCls, _ = make_fake_feed_cls(section="feed", is_stale=True, has_new_data=True)
    last_runs = {}
    driver = EventFeedDriver(make_config(), last_runs, MagicMock())

    driver._drive_one(FakeCls)

    assert "feed" in last_runs


def test_collect_event_feeds_drives_the_COLLECTORS_tuple():
    from atmos_gl import collectors

    with patch("atmos_gl.collectors.EventFeedDriver") as MockDriver:
        collectors.collect_event_feeds(make_config(), {})

    MockDriver.return_value.drive.assert_called_once_with(collectors.COLLECTORS)


def test_collect_file_caches_drives_the_CACHE_COLLECTORS_tuple():
    from atmos_gl import collectors

    with patch("atmos_gl.collectors.EventFeedDriver") as MockDriver:
        collectors.collect_file_caches(make_config(), {})

    MockDriver.return_value.drive.assert_called_once_with(collectors.CACHE_COLLECTORS)


# ---------------------------------------------------------------------------
# FieldCollectorDriver
# ---------------------------------------------------------------------------

def make_fake_field_cls(status_name="gfs_atmos"):
    instance = MagicMock()
    FakeCls = MagicMock(return_value=instance)
    FakeCls.status_name = status_name
    FakeCls.__name__ = "FakeFieldCollector"
    return FakeCls, instance


def test_field_collector_driver_gate_and_status_key_are_both_status_name():
    FakeCls, _ = make_fake_field_cls(status_name="rtofs_currents")
    driver = FieldCollectorDriver(make_config(), store=MagicMock(), ctx=MagicMock())

    assert driver._gate_key(FakeCls) == "rtofs_currents"
    assert driver._status_key(FakeCls) == "rtofs_currents"


def test_field_collector_driver_constructs_with_config_and_store_then_collects_with_ctx():
    FakeCls, instance = make_fake_field_cls()
    store = MagicMock()
    ctx = MagicMock()
    cfg = make_config()
    driver = FieldCollectorDriver(cfg, store, ctx, MagicMock())

    driver._drive_one(FakeCls)

    FakeCls.assert_called_once_with(cfg, store)
    instance.collect.assert_called_once_with(ctx)


def test_field_collector_driver_calls_start_then_collect_then_record_in_order():
    """Fixes the previously-missing 'running' indicator for field collectors -- they
    never showed as running in the Data Status UI mid-fetch, unlike event feeds, before
    this extraction."""
    FakeCls, instance = make_fake_field_cls(status_name="gfs_atmos")
    adapter = MagicMock()
    driver = FieldCollectorDriver(
        make_config(), store=MagicMock(), ctx=MagicMock(), process_status_adapter=adapter
    )
    manager = MagicMock()
    manager.attach_mock(adapter.record_process_start, "record_process_start")
    manager.attach_mock(instance.collect, "collect")
    manager.attach_mock(adapter.record_process_run, "record_process_run")

    driver._drive_one(FakeCls)

    assert [c[0] for c in manager.mock_calls] == [
        "record_process_start", "collect", "record_process_run",
    ]
    adapter.record_process_start.assert_called_once_with("gfs_atmos", "collector")
    adapter.record_process_run.assert_called_once_with("gfs_atmos", "collector", success=True)


def make_bare_service():
    from atmos_gl.collectors.service import CollectorService

    svc = CollectorService.__new__(CollectorService)
    svc.config = make_config()
    svc.store = MagicMock()
    svc.process_status_adapter = MagicMock()
    return svc


def test_collect_fields_drives_FIELD_COLLECTOR_CLASSES_via_field_collector_driver():
    from atmos_gl.collectors import FIELD_COLLECTOR_CLASSES

    svc = make_bare_service()

    with patch("atmos_gl.collectors.service.FieldCollectorDriver") as MockDriver:
        svc._collect_fields()

    MockDriver.assert_called_once()
    args, kwargs = MockDriver.call_args
    assert args[0] is svc.config
    assert args[1] is svc.store
    assert kwargs["process_status_adapter"] is svc.process_status_adapter
    MockDriver.return_value.drive.assert_called_once_with(FIELD_COLLECTOR_CLASSES)
