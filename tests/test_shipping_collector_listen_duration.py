#!/usr/bin/env python3
"""Tests for ShippingCollector._listen_duration_seconds() -- the "Listen duration"
slider (shipping_collector.listen_duration, 5-60) is stored/edited in MINUTES, but
run()'s per-slice AIS listen time (and heartbeat_period_s, via it) need seconds.
"""
from atmos_gl.collectors.shipping import ShippingCollector


def make_collector(settings=None):
    c = ShippingCollector.__new__(ShippingCollector)
    c.settings = settings or {}
    return c


def test_listen_duration_seconds_converts_minutes_to_seconds():
    c = make_collector(settings={"listen_duration": 15})
    assert c._listen_duration_seconds() == 15 * 60.0


def test_listen_duration_seconds_defaults_to_five_minutes():
    c = make_collector(settings={})
    assert c._listen_duration_seconds() == 5 * 60.0


def test_listen_duration_seconds_clamps_to_the_slider_range():
    assert make_collector({"listen_duration": 1})._listen_duration_seconds() == 5 * 60.0
    assert make_collector({"listen_duration": 999})._listen_duration_seconds() == 60 * 60.0


def test_listen_duration_seconds_falls_back_on_non_numeric_value():
    c = make_collector(settings={"listen_duration": "not-a-number"})
    assert c._listen_duration_seconds() == 5 * 60.0
