#!/usr/bin/env python3
"""Tests for ShippingCollector._sleep_interval_seconds()/heartbeat_period_s -- the
"Sleep interval" slider (shipping_collector.sleep_interval, 5-30) is stored/edited in
MINUTES, but run()'s asyncio.sleep() and heartbeat cadence need seconds.
"""
from atmos_gl.collectors.shipping import ShippingCollector


def make_collector(settings=None):
    c = ShippingCollector.__new__(ShippingCollector)
    c.settings = settings or {}
    return c


def test_sleep_interval_seconds_converts_minutes_to_seconds():
    c = make_collector(settings={"sleep_interval": 12})
    assert c._sleep_interval_seconds() == 12 * 60.0


def test_sleep_interval_seconds_defaults_to_five_minutes():
    c = make_collector(settings={})
    assert c._sleep_interval_seconds() == 5 * 60.0


def test_sleep_interval_seconds_clamps_to_the_slider_range():
    assert make_collector({"sleep_interval": 1})._sleep_interval_seconds() == 5 * 60.0
    assert make_collector({"sleep_interval": 99})._sleep_interval_seconds() == 30 * 60.0


def test_sleep_interval_seconds_falls_back_on_non_numeric_value():
    c = make_collector(settings={"sleep_interval": "not-a-number"})
    assert c._sleep_interval_seconds() == 5 * 60.0


def test_heartbeat_period_s_is_unaffected_by_sleep_interval():
    """heartbeat_period_s is derived from listen_duration (heartbeats are recorded per
    SLICE, ten times a rotation, before sleep_interval's pause ever happens) -- changing
    sleep_interval must not change it."""
    c = make_collector(settings={"listen_duration": 5, "sleep_interval": 30})
    assert c.heartbeat_period_s == (5 * 60.0) * 2.0  # base_seconds * max SLICE_DENSITY_MAP weight (2.0)
