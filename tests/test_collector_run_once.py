#!/usr/bin/env python3
"""Tests for collectors/run_once.py (`make collect`) and the cross-process collector
lock it shares with the scheduled sweep (db/collector_lock.py)."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from atmos_gl.collectors import CACHE_COLLECTORS, COLLECTORS
from atmos_gl.collectors.run_once import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    run_once,
    runnable_collectors,
)
from atmos_gl.db.collector_lock import collector_lock


def make_config(channel_enabled=None):
    cfg = MagicMock()
    cfg.get_setting.return_value = channel_enabled or {}
    return cfg


def make_collector_cls(section="demo", channel_key="demo", has_new_data=True, collect_error=None):
    instance = MagicMock()
    instance.has_new_data.return_value = has_new_data
    if collect_error:
        instance.collect.side_effect = collect_error
    cls = MagicMock(return_value=instance, section=section, channel_key=channel_key)
    return cls, instance


class _RecordingLock:
    def __init__(self):
        self.calls = []

    @contextmanager
    def __call__(self, name, wait=False, on_wait=None, bind=None):
        self.calls.append((name, wait))
        yield True


def _run(cls, config=None, force=False):
    lock, status = _RecordingLock(), MagicMock()
    with patch("atmos_gl.collectors.run_once.runnable_collectors",
               return_value={cls.section: cls}):
        code = run_once(config or make_config(), cls.section, force=force,
                        lock=lock, process_status_adapter=status)
    return code, lock, status


def test_every_event_feed_and_file_cache_is_runnable_by_a_unique_section():
    expected = {c.section for c in (*COLLECTORS, *CACHE_COLLECTORS)}
    assert len(expected) == len(COLLECTORS) + len(CACHE_COLLECTORS)  # no clashes
    assert set(runnable_collectors()) == expected
    assert "world_events" in expected


def test_unknown_name_lists_the_valid_ones(capsys):
    assert run_once(make_config(), "nope", lock=_RecordingLock(),
                    process_status_adapter=MagicMock()) == EXIT_USAGE
    assert "world_events" in capsys.readouterr().out


def test_runs_collect_and_records_status_under_the_lock():
    cls, instance = make_collector_cls()
    code, lock, status = _run(cls)
    assert code == EXIT_OK
    instance.collect.assert_called_once_with()
    assert lock.calls == [("demo", True)]  # waits for an in-progress scheduled run
    status.record_process_start.assert_called_once_with("demo", "collector")
    status.record_process_run.assert_called_once_with("demo", "collector", success=True)


def test_skips_when_nothing_new_unless_forced():
    cls, instance = make_collector_cls(has_new_data=False)
    code, _, status = _run(cls)
    assert code == EXIT_OK
    instance.collect.assert_not_called()
    status.record_process_start.assert_not_called()

    cls, instance = make_collector_cls(has_new_data=False)
    _run(cls, force=True)
    instance.collect.assert_called_once_with()
    instance.has_new_data.assert_not_called()


def test_refuses_a_disabled_channel_unless_forced():
    cls, instance = make_collector_cls(channel_key="chan")
    code, lock, _ = _run(cls, config=make_config({"chan": False}))
    assert code == EXIT_FAILED
    cls.assert_not_called()
    assert lock.calls == []

    cls, instance = make_collector_cls(channel_key="chan")
    assert _run(cls, config=make_config({"chan": False}), force=True)[0] == EXIT_OK
    instance.collect.assert_called_once_with()


def test_a_failing_collect_is_recorded_and_returns_failure():
    cls, _ = make_collector_cls(collect_error=RuntimeError("upstream 500"))
    code, _, status = _run(cls)
    assert code == EXIT_FAILED
    status.record_process_run.assert_called_once_with(
        "demo", "collector", success=False, error="upstream 500"
    )


# ---- the real advisory lock -----------------------------------------------------------

def test_collector_lock_excludes_a_second_holder_until_released(real_db):
    with collector_lock("lock-test", bind=real_db) as first:
        assert first is True
        with collector_lock("lock-test", bind=real_db) as second:
            assert second is False  # busy, and not waiting
        with collector_lock("other-collector", bind=real_db) as unrelated:
            assert unrelated is True  # locks are per collector
    with collector_lock("lock-test", bind=real_db) as again:
        assert again is True  # released on exit


def test_collector_lock_wait_calls_on_wait_then_blocks_until_free(real_db):
    import threading

    order = []
    released = threading.Event()
    holder_ready = threading.Event()

    def holder():
        with collector_lock("wait-test", bind=real_db):
            holder_ready.set()
            released.wait(5)
            order.append("holder released")

    t = threading.Thread(target=holder)
    t.start()
    holder_ready.wait(5)

    def on_wait():
        order.append("waiting")
        released.set()

    with collector_lock("wait-test", wait=True, on_wait=on_wait, bind=real_db) as acquired:
        order.append("acquired")
        assert acquired is True
    t.join(5)
    assert order == ["waiting", "holder released", "acquired"]


@pytest.fixture(autouse=True)
def _no_real_status_writes():
    """run_once defaults to the real ProcessStatusAdapter; every test above injects
    a mock, this just guarantees none slips through to a database."""
    with patch("atmos_gl.collectors.run_once.ProcessStatusAdapter", side_effect=AssertionError):
        yield
