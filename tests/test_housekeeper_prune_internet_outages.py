#!/usr/bin/env python3
"""Tests for Housekeeper.prune_expired_internet_outages_events, mirroring
test_housekeeper_prune_gdacs.py's wiring-test pattern. The adapter-level behaviour (what
is deleted, ongoing events never) is covered by
test_internet_outages_adapter_real_vs_fake.py."""

from unittest.mock import MagicMock, patch

from terrascope.housekeeper import Housekeeper


def make_bare_housekeeper():
    return Housekeeper.__new__(Housekeeper)


def test_prune_expired_internet_outages_events_noop_on_falsy_expiry():
    hk = make_bare_housekeeper()
    with patch("terrascope.housekeeper.InternetOutagesAdapter") as MockAdapter:
        hk.prune_expired_internet_outages_events(0)
    MockAdapter.assert_not_called()


def test_prune_expired_internet_outages_events_asks_the_adapter_to_prune_by_days():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.prune_expired.return_value = 3
    with patch("terrascope.housekeeper.InternetOutagesAdapter", return_value=adapter):
        hk.prune_expired_internet_outages_events(7)
    adapter.prune_expired.assert_called_once_with(7)


def test_prune_expired_internet_outages_events_swallows_adapter_errors():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.prune_expired.side_effect = RuntimeError("db down")
    with patch("terrascope.housekeeper.InternetOutagesAdapter", return_value=adapter):
        hk.prune_expired_internet_outages_events(7)  # must not raise
