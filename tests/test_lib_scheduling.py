#!/usr/bin/env python3
"""Tests for lib/scheduling.py (architecture review candidate "one long-running-
service scaffold"). interval_elapsed replaces the identical `last_run is None or
(now - last_run) >= interval_s` check hand-duplicated in Housekeeper.run() and
CollectorService.run() -- neither had test coverage for this logic before.
"""
from worldmap.lib.scheduling import interval_elapsed


def test_interval_elapsed_true_when_never_run():
    assert interval_elapsed(None, now=1000.0, interval_s=3600) is True


def test_interval_elapsed_false_when_interval_not_yet_reached():
    assert interval_elapsed(last_run=1000.0, now=1500.0, interval_s=3600) is False


def test_interval_elapsed_true_when_interval_exactly_reached():
    assert interval_elapsed(last_run=1000.0, now=4600.0, interval_s=3600) is True


def test_interval_elapsed_true_when_well_past_interval():
    assert interval_elapsed(last_run=1000.0, now=100000.0, interval_s=3600) is True


def test_interval_elapsed_works_with_monotonic_style_clocks():
    """Confirms the function is genuinely clock-agnostic -- CollectorService passes
    asyncio.get_event_loop().time() (monotonic), Housekeeper passes time.time() (wall
    clock); interval_elapsed doesn't care which, as long as both args use the same one."""
    assert interval_elapsed(last_run=0.0, now=59.9, interval_s=60.0) is False
    assert interval_elapsed(last_run=0.0, now=60.0, interval_s=60.0) is True
