#!/usr/bin/env python3
"""Tests for LightningCollector._sleep_interval_seconds()/heartbeat_period_s -- the
"Sleep interval" slider (lightning_collector.sleep_interval, 5-30) is stored/edited in
MINUTES, but run()'s asyncio.sleep() and heartbeat cadence need seconds. Previously a
hardcoded 600s sleep with a fixed 900s heartbeat allowance; both must now scale with the
configured value, not just the sleep itself, or a long sleep_interval would make the
Data Status UI falsely report the collector as stale between scans.
"""
from atmos_gl.collectors.lightning import LightningCollector


def make_collector(settings=None):
    c = LightningCollector.__new__(LightningCollector)
    c.settings = settings or {}
    return c


def test_sleep_interval_seconds_converts_minutes_to_seconds():
    c = make_collector(settings={"sleep_interval": 20})
    assert c._sleep_interval_seconds() == 20 * 60.0


def test_sleep_interval_seconds_defaults_to_ten_minutes():
    """Preserves the original hardcoded 600s (10 minute) behaviour when unset."""
    c = make_collector(settings={})
    assert c._sleep_interval_seconds() == 10 * 60.0


def test_sleep_interval_seconds_clamps_to_the_slider_range():
    assert make_collector({"sleep_interval": 0})._sleep_interval_seconds() == 5 * 60.0
    assert make_collector({"sleep_interval": 45})._sleep_interval_seconds() == 30 * 60.0


def test_sleep_interval_seconds_falls_back_on_non_numeric_value():
    c = make_collector(settings={"sleep_interval": None})
    assert c._sleep_interval_seconds() == 10 * 60.0


def test_heartbeat_period_s_scales_with_sleep_interval():
    """A fixed heartbeat allowance (the old 900s) would fall short once sleep_interval
    exceeds ~10 minutes, since only one heartbeat is recorded per scan-then-sleep cycle
    -- this must track the configured interval, not a hardcoded constant."""
    short = make_collector(settings={"sleep_interval": 5})
    long = make_collector(settings={"sleep_interval": 30})
    assert short.heartbeat_period_s == 5 * 60.0 + 300.0
    assert long.heartbeat_period_s == 30 * 60.0 + 300.0
    assert long.heartbeat_period_s > short.heartbeat_period_s
