#!/usr/bin/env python3
"""Tests for ShippingCollector.refresh_settings()'s self.url caching -- architecture
review Candidate 2: self.url now derives from source_url() (the same method the Data
Status link uses) instead of an independent self.datasource_url("shipping") call, so
the two can no longer silently disagree.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.shipping import ShippingCollector


def make_collector(url=None):
    c = ShippingCollector.__new__(ShippingCollector)
    c.section = "shipping_collector"
    c.config = MagicMock()
    c.config.get_section.return_value = {}
    c.config.get_setting.return_value = {"shipping": url} if url else {}
    return c


def test_refresh_settings_sets_url_from_source_url():
    c = make_collector(url="wss://stream.example/v0")
    c.refresh_settings()
    assert c.url == "wss://stream.example/v0"


def test_refresh_settings_sets_empty_string_when_unconfigured():
    """Preserves the pre-refactor type contract (a str, not None) -- websockets.
    connect() is called with self.url directly."""
    c = make_collector()
    c.refresh_settings()
    assert c.url == ""
