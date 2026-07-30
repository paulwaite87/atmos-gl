#!/usr/bin/env python3
"""Tests for StormsCollector.source_url() -- storms' two ATCF mirror URLs live in
data_collector.datasources under two keys (jtwc/nhc), same as every other collector's
own URL, rather than in storms' own config section -- it overrides
CollectorBase.source_url()'s single-datasource_key lookup to try both, JTWC first.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.storms import StormsCollector


def make_collector(jtwc_url=None, nhc_url=None):
    c = StormsCollector.__new__(StormsCollector)
    c.config = MagicMock()
    datasources = {}
    if jtwc_url is not None:
        datasources["jtwc"] = jtwc_url
    if nhc_url is not None:
        datasources["nhc"] = nhc_url
    c.config.get_setting.return_value = datasources
    return c


def test_source_url_prefers_jtwc():
    c = make_collector(jtwc_url="https://jtwc.example", nhc_url="https://nhc.example")

    assert c.source_url() == "https://jtwc.example"


def test_source_url_falls_back_to_nhc_when_jtwc_unset():
    c = make_collector(nhc_url="https://nhc.example")

    assert c.source_url() == "https://nhc.example"


def test_source_url_none_when_neither_configured():
    c = make_collector()

    assert c.source_url() is None
