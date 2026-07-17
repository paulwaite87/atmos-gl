#!/usr/bin/env python3
"""Tests for StormsCollector.source_url() -- storms keeps its two ATCF mirror URLs
(jtwc_url/nhc_url) directly in its own config section rather than in
data_collector.datasources, so it overrides CollectorBase.source_url()'s default
datasource_key lookup instead of using it.
"""
from atmos_gl.collectors.storms import StormsCollector


def make_collector(settings=None):
    c = StormsCollector.__new__(StormsCollector)
    c.settings = settings or {}
    return c


def test_source_url_prefers_jtwc():
    c = make_collector({"jtwc_url": "https://jtwc.example/", "nhc_url": "https://nhc.example/"})

    assert c.source_url() == "https://jtwc.example/"


def test_source_url_falls_back_to_nhc_when_jtwc_unset():
    c = make_collector({"nhc_url": "https://nhc.example/"})

    assert c.source_url() == "https://nhc.example/"


def test_source_url_none_when_neither_configured():
    c = make_collector({})

    assert c.source_url() is None
