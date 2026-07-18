#!/usr/bin/env python3
"""Tests for LightningCollector.refresh_settings()'s self.url caching -- architecture
review Candidate 2: self.url now derives from source_url() (the same method the Data
Status link uses) instead of an independent self.datasource_url("lightning") call, so
the two can no longer silently disagree.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.lightning import LightningCollector


def make_collector(url=None):
    c = LightningCollector.__new__(LightningCollector)
    c.section = "lightning_collector"
    c.config = MagicMock()
    c.config.get_section.return_value = {}
    # Also backs the primary_region_label read (config.get_setting("common", "region"))
    # -- unused/unasserted here, so a blanket return_value is fine for both calls.
    c.config.get_setting.return_value = {"lightning": url} if url else {}
    return c


def test_refresh_settings_sets_url_from_source_url():
    c = make_collector(url="https://openweather.example/lightning")
    c.refresh_settings()
    assert c.url == "https://openweather.example/lightning"


def test_refresh_settings_sets_empty_string_when_unconfigured():
    """Preserves the pre-refactor type contract (a str, not None) -- fetch_and_store()
    passes self.url directly to session.get()."""
    c = make_collector()
    c.refresh_settings()
    assert c.url == ""
