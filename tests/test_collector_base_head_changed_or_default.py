#!/usr/bin/env python3
"""Tests for CollectorBase._head_changed_or_default (architecture review candidate
"collapse the _head_changed wrapper duplication"). Collapses the identical
has_new_data() wrapper hand-duplicated in quakes.py, volcanoes.py, and satellites.py --
none of which had test coverage for this logic before. storms.py is not a caller (it
HEADs two URLs and logs one combined message, not the single-URL shape this wraps), so
it's out of scope here.
"""
import logging
from unittest.mock import patch

from worldmap.collectors.base import CollectorBase


def test_returns_true_when_head_changed_reports_true():
    with patch.object(CollectorBase, "_head_changed", return_value=True):
        assert CollectorBase._head_changed_or_default("http://example.com", "Quakes") is True


def test_returns_true_when_head_probe_fails():
    """_head_changed returning None (probe failed) defaults to True -- collect anyway,
    the safe fallback -- matching every one of the 3 original wrappers."""
    with patch.object(CollectorBase, "_head_changed", return_value=None):
        assert CollectorBase._head_changed_or_default("http://example.com", "Quakes") is True


def test_returns_false_and_logs_when_unchanged(caplog):
    with patch.object(CollectorBase, "_head_changed", return_value=False):
        with caplog.at_level(logging.DEBUG, logger="worldmap.collectors.base"):
            result = CollectorBase._head_changed_or_default("http://example.com", "Volcanoes")

    assert result is False
    assert "Volcanoes: remote unchanged; skipping collect." in caplog.text


def test_label_is_used_verbatim_in_the_log_message(caplog):
    with patch.object(CollectorBase, "_head_changed", return_value=False):
        with caplog.at_level(logging.DEBUG, logger="worldmap.collectors.base"):
            CollectorBase._head_changed_or_default("http://example.com", "Satellites")

    assert "Satellites: remote unchanged; skipping collect." in caplog.text
