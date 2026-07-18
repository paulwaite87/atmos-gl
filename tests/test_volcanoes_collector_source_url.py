#!/usr/bin/env python3
"""Tests for VolcanoesCollector's use of source_url() -- architecture review Candidate
2 removed the collector's own base_url() (a pure duplicate of source_url(), re-spelling
"volcanoes" as a literal instead of referencing datasource_key), so has_new_data()/
collect() now call source_url() directly, the same method the Data Status page uses.
"""
from unittest.mock import MagicMock

from atmos_gl.collectors.volcanoes import VolcanoesCollector


def make_collector(url=None):
    c = VolcanoesCollector.__new__(VolcanoesCollector)
    c.config = MagicMock()
    c.config.get_setting.return_value = {"volcanoes": url} if url else {}
    return c


def test_has_new_data_returns_true_with_no_url_configured():
    c = make_collector()
    assert c.has_new_data() is True


def test_has_new_data_uses_source_url_for_the_head_check():
    c = make_collector(url="https://hazel.example/api")
    c._head_changed_or_default = MagicMock(return_value=False)

    result = c.has_new_data()

    c._head_changed_or_default.assert_called_once_with("https://hazel.example/api", "Volcanoes")
    assert result is False


def test_collect_fetches_from_source_url():
    c = make_collector(url="https://hazel.example/api")
    c.volcano_adapter = MagicMock()
    c._fetch_all = MagicMock(return_value=[])

    c.collect()

    c._fetch_all.assert_called_once_with("https://hazel.example/api")
