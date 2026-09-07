#!/usr/bin/env python3
"""Tests for CollectorBase._get (architecture review candidate "shared GET-fetch
module"). Collapses the GET-fetch duplication hand-rolled across quakes.py/
satellites.py/storms.py/volcanoes.py/world_events.py -- four different styles, one
(storms.py) silently missing the User-Agent header entirely -- into one seam, the
same shape of duplication _head_changed already collapsed for HEAD requests.
"""
import logging
from unittest.mock import MagicMock, patch

import requests

from atmos_gl.collectors.base import CollectorBase


def _fake_response(status_code=200):
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status.side_effect = (
        requests.HTTPError(f"HTTP {status_code}") if status_code >= 400 else None
    )
    return r


def test_returns_the_response_on_success():
    resp = _fake_response()
    with patch("requests.get", return_value=resp):
        result = CollectorBase._get("http://example.com")

    assert result is resp


def test_sets_the_standard_user_agent():
    with patch("requests.get", return_value=_fake_response()) as mock_get:
        CollectorBase._get("http://example.com")

    assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "AtmosGL-Collector/1.0"


def test_caller_supplied_headers_are_merged_not_replaced():
    with patch("requests.get", return_value=_fake_response()) as mock_get:
        CollectorBase._get("http://example.com", headers={"Accept": "application/json"})

    assert mock_get.call_args.kwargs["headers"] == {
        "User-Agent": "AtmosGL-Collector/1.0",
        "Accept": "application/json",
    }


def test_default_timeout_is_15_seconds():
    with patch("requests.get", return_value=_fake_response()) as mock_get:
        CollectorBase._get("http://example.com")

    assert mock_get.call_args.kwargs["timeout"] == 15


def test_timeout_and_other_kwargs_pass_through():
    with patch("requests.get", return_value=_fake_response()) as mock_get:
        CollectorBase._get("http://example.com", timeout=60, stream=True)

    assert mock_get.call_args.kwargs["timeout"] == 60
    assert mock_get.call_args.kwargs["stream"] is True


def test_returns_none_and_logs_on_connection_error(caplog):
    with patch("requests.get", side_effect=requests.ConnectionError("down")):
        with caplog.at_level(logging.ERROR, logger="atmos_gl.collectors.base"):
            result = CollectorBase._get("http://example.com")

    assert result is None
    assert "http://example.com" in caplog.text


def test_returns_none_on_non_2xx_status():
    with patch("requests.get", return_value=_fake_response(status_code=404)):
        result = CollectorBase._get("http://example.com")

    assert result is None
